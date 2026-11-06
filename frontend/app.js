import {icon} from './icons.js';
import {demoQuestions} from './demo.js';

const $ = (selector, root=document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const multiline = value => esc(value).replace(/\n/g, '<br>');
const state = {demo:true, view:'upload', tab:'marking', question:demoQuestions[2], questions:[],
  submissions:[], submission:null, activeAnswer:null, grade:null, document:null, page:0,
  pageUrls:new Map(), zoom:100, selecting:false, regions:[], transcript:null, answerDraft:'', session:null,
  busy:'', evidence:[], revisionMode:false};
const viewTitles = {upload:'Upload a response',overview:'My submissions',workspace:'Marking workspace',bank:'Question bank',progress:'Learning progress'};
const labelOf = q => q?.label || q?.q_id?.match(/-q(.+)$/)?.[1]?.replace(/^(\d+)([a-z])$/, '$1($2)') || 'Question';
const sourceOf = q => {const m=q.q_id?.match(/4ec1-(01r?)-(\d{4})-(\d{2})-/);return m?`${({'01':'Jan','06':'Jun','11':'Nov'})[m[3]]||m[3]} ${m[2]} · Paper ${m[1]==='01r'?'1R':'1'}`:'IGCSE Economics';};
const currentQ = () => state.question;
const btn = (action, label, symbol='', kind='', extra='') => `<button class="btn ${kind}" data-action="${action}" ${extra}>${symbol?icon(symbol):''}${label}</button>`;
const empty = (title, copy, symbol='folder', actions='') => `<div class="empty-state">${icon(symbol)}<h2>${esc(title)}</h2><p>${esc(copy)}</p>${actions}</div>`;

async function api(path, options={}) {
  const headers = {...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData)) {headers['Content-Type']='application/json'; options.body=JSON.stringify(options.body);}
  const response = await fetch(`/api/v1${path}`, {...options, headers});
  let data;
  try {data=await response.json();} catch {data={};}
  if (!response.ok) {
    throw new Error(typeof data.detail==='string' ? data.detail : Array.isArray(data.detail) ? data.detail.map(x=>x.msg).join('. ') : 'The request could not be completed. Please try again.');
  }
  return data;
}

function toast(message, error=false) {
  $('#toast').className=`toast ${error?'error':''}`;$('#toast').textContent=message;
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>{$('#toast').className='';$('#toast').textContent='';},6000);
}
function modal(title, content) {
  const dialog=$('#modal');
  dialog.innerHTML=`<div class="modal-heading"><h2 id="dialog-title">${esc(title)}</h2><button class="icon-button" data-action="close-modal" aria-label="Close dialog">${icon('close')}</button></div><div class="modal-body">${content}</div>`;
  if (!dialog.open) dialog.showModal();
}
function clearPageUrls() {for (const url of state.pageUrls.values()) URL.revokeObjectURL(url);state.pageUrls.clear();}
function resetWorkspace() {
  clearPageUrls();state.question=state.demo?demoQuestions[2]:null;state.submission=null;state.activeAnswer=null;
  state.grade=null;state.document=null;state.page=0;state.regions=[];state.transcript=null;state.session=null;
  state.answerDraft='';state.revisionMode=false;state.tab='marking';state.failedJob=null;
}

function syncWorkspaceRoute() {
  const params=new URLSearchParams();
  if (!state.demo) {
    if(state.submission)params.set('submission',state.submission.id);
    else if(state.document){params.set('document',state.document.id);if(state.question?.id!=='question-scan')params.set('question',state.question.id);}
    else if(state.question)params.set('question',state.question.id);
  }
  history.replaceState(null,'',`#workspace${params.size?'?'+params.toString():''}`);
}

function render() {
  const nav=[['upload','upload','Upload a response'],['workspace','mark','Marking workspace'],['overview','folder','My submissions'],['bank','book','Question bank'],['progress','chart','Learning progress']];
  $('#app').innerHTML=`<div class="app-shell">
    <aside class="sidebar" aria-label="Main navigation">
      <a class="university" href="https://www.polyu.edu.hk/" target="_blank" rel="noopener noreferrer" aria-label="The Hong Kong Polytechnic University">
        <span class="emblem"><img src="/assets/polyu-logo.svg" alt="PolyU emblem"></span>
        <span class="university-name"><b>香港理工大學</b>The Hong Kong<br>Polytechnic University</span>
      </a>
      <div class="product"><strong>Econ<span>Review</span><span style="font-size:12px;margin-left:3px">.</span></strong><span class="eyebrow">Assessment & understanding</span></div>
      <div class="eyebrow nav-label">Your workspace</div>
      <nav class="nav">${nav.map(([key,symbol,label])=>`<button data-action="nav" data-view="${key}" class="${state.view===key?'active':''}" ${state.view===key?'aria-current="page"':''}>${icon(symbol)}${label}</button>`).join('')}</nav>
      <div class="course-card"><div class="course-icon">${icon('book')}</div><strong>IGCSE Economics</strong><p>Pearson · Paper 1<br>Mark, reflect, understand.</p><div class="rule"></div><p>COMP4431 course project</p></div>
      <div class="sidebar-bottom"><button data-action="help">${icon('help')}Help & getting started</button><p class="project-note">EconReview · A student project<br>Built for thoughtful feedback.</p></div>
    </aside>
    <div class="main-shell"><header class="topbar"><div style="display:flex;align-items:center"><button class="icon-button mobile-menu" data-action="menu" aria-label="Toggle navigation">${icon('menu')}</button><div class="breadcrumbs">Workspace ${icon('chevron')}<strong>IGCSE Economics</strong>${icon('chevron')}<span>${viewTitles[state.view]}</span></div></div><div class="topbar-right">${state.demo&&state.view==='workspace'?'<span class="sample-pill">Sample workspace</span>':'<span class="local-label"><span></span>Local workspace</span>'}<button class="icon-button" data-action="help" aria-label="Help">${icon('help')}</button></div></header>
      <main class="content" id="main"><div class="page-heading"><div><h1>${viewTitles[state.view]}</h1><p>${({upload:'Your next step starts with an answer.',workspace:'Click a missed point. Let’s work through it together.',overview:'Your Economics work, all in one place.',bank:'The right question. The right marking guidance.',progress:'Small checks. A clearer picture of your understanding.'})[state.view]}</p></div><div class="heading-actions">${state.view==='upload'?btn('sample-open','Explore an example','arrow',''):state.view==='workspace'?`${btn('new-practice','Type an answer','text','ghost')}${btn('upload','Upload response','upload','primary')}`:btn('upload','Upload response','upload','primary')}</div></div>
      ${state.view==='upload'?renderUpload():state.view==='workspace'?renderWorkspace():state.view==='overview'?renderOverview():state.view==='bank'?renderBank():renderProgress()}
      <div class="footer-line"><span>${icon('lock')} Your work stays in your workspace.</span><span>COMP4431 · The Hong Kong Polytechnic University</span></div></main>
    </div></div>`;
  bindSelection();
}

function railQuestions() {
  if (state.demo) return demoQuestions;
  const ids=new Set(state.submission?.answers.map(a=>a.question_id) || []);
  if (state.question) ids.add(state.question.id);
  return ids.size?state.questions.filter(q=>ids.has(q.id)):state.questions.slice(0,4);
}

function renderWorkspace() {
  const q=currentQ();
  if (!q) return empty('Ready for your next answer','Choose a question to start, or open a previous submission.','mark',btn('new-practice','Choose a question','book','primary'));
  const questions=railQuestions(), file=state.document?.filename || (state.demo?'June 2025 · Paper 1R':'Independent practice');
  const hasText=!!state.transcript || !!state.activeAnswer || state.demo;
  return `<div class="pipeline"><div class="steps"><span class="step ${state.document||state.demo?'done':'current'}"><span class="step-dot">${state.document||state.demo?icon('check'):'1'}</span>Upload response</span><span class="step ${hasText?'done':state.document?'current':''}"><span class="step-dot">${hasText?icon('check'):'2'}</span>Review text</span><span class="step ${state.grade||state.demo?'current':''}"><span class="step-dot">3</span>Mark & learn</span></div><span class="pipeline-note">${icon('lock')}${state.demo?'Illustrative response · not a real grade':'Saved on this computer'}</span></div>
    <section class="workspace" aria-label="Document and marking workspace"><div class="workspace-heading"><div class="file-heading">${icon('mark')}<span>${esc(file)}</span><small>${state.demo?'Example response':state.document?`${state.document.pages.length} page${state.document.pages.length===1?'':'s'}`:'Typed answer'}</small></div><div class="workspace-heading-right"><span class="status-label">${icon(hasText?'check':'clock')}${hasText?'Text confirmed':'Review required'}</span><button class="icon-button" data-action="help" aria-label="Workspace help">${icon('more')}</button></div></div>
      <div class="workspace-body"><aside class="question-rail" aria-label="Questions"><div class="eyebrow">Questions <span style="float:right">${String(questions.length).padStart(2,'0')}</span></div>${questions.map(question=>`<button class="question-item ${question.id===q.id?'selected':''}" data-action="question" data-id="${esc(question.id)}" ${question.id===q.id?'aria-current="true"':''}><span class="mini-check ${question.id===q.id?'review':state.demo&&question.score!==null?'done':''}">${icon(question.id===q.id?'check':state.demo&&question.score!==null?'check':'circle')}</span><span><span class="question-number">Q${esc(labelOf(question))}</span><span class="question-subtitle">${question.marks} marks</span></span></button>`).join('')}<div class="rail-divider"></div><p class="rail-summary">${state.demo?'3 of 4<br>examples reviewed':`${questions.length} question${questions.length===1?'':'s'}<br>in this workspace`}</p></aside>
        <div class="viewer"><div class="viewer-toolbar"><span class="toolbar-label">${icon(state.document?'scan':'mark')}${state.document?'Original scan':state.demo?'Student response':'Your response'}</span><div class="zoom-controls"><button class="icon-button" data-action="zoom-out" aria-label="Zoom out">${icon('minus')}</button><span>${state.zoom}%</span><button class="icon-button" data-action="zoom-in" aria-label="Zoom in">${icon('plus')}</button></div></div><div class="document-scroll" id="document-scroll">${renderDocument(q)}</div><div class="viewer-bottom"><div class="page-controls">${icon('mark')}${state.document?`<select id="page-select" aria-label="Document page">${state.document.pages.map((p,i)=>`<option value="${i}" ${i===state.page?'selected':''}>Page ${i+1} / ${state.document.pages.length}</option>`).join('')}</select>`:'Page 1 of 1'}</div><button data-action="select-region">${icon('crop')}${state.selecting?'Finish selection':state.document?'Select answer region':'Answer region'}</button>${state.document?`<button data-action="clear-regions">Clear (${state.regions.length})</button>`:''}</div></div>
        <aside class="marking-panel" aria-label="Marking and feedback"><div class="panel-tabs" role="tablist">${[['marking','mark','Marking'],['text','text','Text review'],['tutor','sparkle','Tutor']].map(([key,symbol,title])=>`<button role="tab" aria-selected="${state.tab===key}" class="${state.tab===key?'active':''}" data-action="panel-tab" data-tab="${key}">${icon(symbol)}${title}</button>`).join('')}</div><div class="panel-inner">${state.busy?`<div class="notice loading-dot">${esc(state.busy)}</div>`:''}${state.tab==='marking'?renderMarking(q):state.tab==='text'?renderTextReview(q):renderTutor(q)}</div><div class="panel-footer"><span>${icon('lock')} ${state.demo?'Sample feedback':'Rubric-grounded feedback'}</span><span>${state.demo?'Example only':'Your personal practice'}</span></div></aside></div>
    </section>`;
}

function renderDocument(q) {
  if (state.document) {
    const src=state.pageUrls.get(`${state.document.id}:${state.page}`);
    const regions=state.regions.filter(r=>r.page===state.page);
    return `<div class="scan-stage ${state.selecting?'selecting':''}" id="scan-stage" style="width:${state.zoom}%">${src?`<img src="${src}" alt="Scanned page ${state.page+1}">`:'<div class="empty-scan">Loading page…</div>'}${regions.map((r,i)=>`<div class="scan-region" data-label="Region ${i+1}" style="left:${r.x*100}%;top:${r.y*100}%;width:${r.width*100}%;height:${r.height*100}%"></div>`).join('')}${state.selecting?'':scanHighlights()}</div>`;
  }
  const handwriting=state.demo && q.id==='example-3'?'<mark>The supply of labour will increase.</mark><br><mark>People will have to keep working for</mark><br>longer because they cannot receive<br>their pension yet.':multiline(q.answer || '');
  return `<article class="exam-paper ${state.demo?'':'typed-paper'}" style="width:${state.zoom}%"><header class="paper-exam"><div><div class="eyebrow">International GCSE</div><h3>ECONOMICS</h3></div><div class="paper-code">Paper 1${state.demo?'R':''}<br>${state.demo?'4EC1/01R':'Practice response'}</div></header><div class="paper-candidate"><span>${state.demo?'Sample student response':'Your answer'}</span><span>${state.demo?'June 2025 · illustrative':'IGCSE Economics'}</span></div><div class="paper-q"><b>${esc(labelOf(q))}</b><div>${q.stimulus?`<p class="stimulus">${esc(q.stimulus)}</p>`:''}${multiline(q.question)}</div></div><div class="paper-marks">(${q.marks} marks)</div>${state.demo?`<div class="answer-lines"><div class="handwriting">${handwriting||'<span class="muted">No answer provided.</span>'}</div>${q.id==='example-3'?'<span class="marker">1</span><span class="marker second">2</span><button class="marker third" data-action="focus-point" data-mp-id="MP3" aria-label="Explore the missing application point">3</button>':''}</div>`:`<textarea id="answer-draft" aria-label="Your answer" placeholder="Write your answer here…" ${state.activeAnswer?'readonly':''}>${esc(state.answerDraft)}</textarea>`}<p class="paper-total">Total for Question ${esc(labelOf(q))} = ${q.marks} marks</p><footer class="paper-footer"><span class="paper-polyu"><span class="emblem"><img src="/assets/polyu-logo.svg" alt=""></span>COMP4431 · Economics practice</span><span>${state.demo?'ILLUSTRATIVE WORKSPACE':'PERSONAL PRACTICE'}</span></footer></article>`;
}

function renderUpload() {
  return `<div class="upload-layout"><section class="upload-card"><div class="upload-card-heading"><span class="upload-symbol">${icon('scan')}</span><div><h2>Bring your answer into focus.</h2><p>A scan, a photo, or simply your own words.</p></div></div><form id="upload-form"><div class="upload-drop" id="upload-drop"><span class="upload-art">${icon('mark')}<span>${icon('plus')}</span></span><h3>Drop your response here</h3><p>or choose a file from your computer</p><label class="btn" for="upload-input">${icon('upload')}Choose a file</label><input type="file" name="file" id="upload-input" accept="image/jpeg,image/png,application/pdf" required><span class="upload-file-label" id="upload-file-label">PDF, JPG or PNG · up to 20 MB</span></div><div class="upload-fields"><label class="form-field"><span>What are you uploading?</span><select name="purpose" id="upload-purpose"><option value="student_answer">A student answer</option><option value="question">A question to find in the bank</option></select></label><label class="form-field" id="upload-question-field"><span>Match it to a question</span><select name="question_id" required><option value="">Choose a question…</option>${questionOptions()}</select><small>The marking guidance comes from the selected question.</small></label></div><div class="upload-form-footer"><span>${icon('lock')}Saved locally. OCR runs when you choose.</span><button class="btn primary" type="submit" ${state.busy?'disabled':''}>${state.busy?'Uploading…':'Open workspace'}${icon('arrow')}</button></div><div class="form-error" id="upload-error" role="alert"></div></form></section><aside class="upload-guide"><div class="guide-brand"><span class="emblem"><img src="/assets/polyu-logo.svg" alt="PolyU emblem"></span><span>COMP4431<br><small>ECONOMICS LEARNING PROJECT</small></span></div><div class="eyebrow">From an answer to understanding</div><h2>More than<br>a mark.</h2><p>Your answer is the beginning.<br>Make the next step count.</p><ol class="guide-steps"><li><span>01</span><div><strong>Confirm what you wrote</strong><p>Check the scanned text, with your original answer beside it.</p></div></li><li><span>02</span><div><strong>See where marks were missed</strong><p>Understand the feedback against the question’s marking points.</p></div></li><li><span>03</span><div><strong>Click a point. Work it through.</strong><p>A tutor probes the concept, explains, and checks again.</p></div></li></ol><div class="guide-footer">${icon('sparkle')}One workspace. A clearer next step.</div></aside></div><div class="type-instead"><span>${icon('text')}No scan? Start with a typed answer.</span>${btn('new-practice','Type an answer','arrow','ghost')}</div>`;
}

function questionOptions(selected='') {
  return state.questions.map(q=>`<option value="${q.id}" ${q.id===selected?'selected':''}>${sourceOf(q)} · Q${esc(labelOf(q))} · ${q.marks} mark${q.marks===1?'':'s'} · ${esc(q.question.slice(0,100))}${q.question.length>100?'…':''}</option>`).join('');
}

function pointTitle(point) {
  const item=state.grade?.marking_points?.find(p=>p.mp_id===point.mp_id);
  return ({identify:'Identify the effect',explain:'Explain the reason',apply:'Apply to the context',define:'Define the concept',analyse:'Develop the analysis',evaluate:'Evaluate the argument',calculate:'Check the calculation'})[item?.type] || point.mp_id;
}

function scanHighlights() {
  // Only map an evidence quote when its words really occur in this OCR page.
  // Missing points and corrected text without a match get no invented location.
  const words=state.document?.ocr?.pages.find(p=>p.page===state.page)?.words || [];
  const norm=text=>text.toLowerCase().replace(/[^\p{L}\p{N}]/gu,'');
  if (!words.length || !state.grade?.result) return '';
  return state.grade.result.point_results.filter(p=>p.status!=='met'&&p.evidence_quote).map(point=>{
    const needle=point.evidence_quote.split(/\s+/).map(norm).filter(Boolean);
    if(!needle.length)return '';
    const index=words.findIndex((word,i)=>needle.every((token,j)=>norm(words[i+j]?.text||'')===token));
    if(index<0)return '';
    const vertices=words.slice(index,index+needle.length).flatMap(w=>w.polygon);
    if(!vertices.length)return '';
    const x=Math.min(...vertices.map(v=>v.x||0)),y=Math.min(...vertices.map(v=>v.y||0));
    const right=Math.max(...vertices.map(v=>v.x||0)),bottom=Math.max(...vertices.map(v=>v.y||0));
    return `<button class="evidence-region" data-action="focus-point" data-mp-id="${esc(point.mp_id)}" aria-label="Explore ${esc(point.mp_id)}: ${esc(point.evidence_quote)}" title="Explore ${esc(point.mp_id)}" style="left:${x*100}%;top:${y*100}%;width:${(right-x)*100}%;height:${(bottom-y)*100}%"><span>${esc(point.mp_id)} ${icon('sparkle')}</span></button>`;
  }).join('');
}

function criterion(title, reason, status, score, mpId, evidence='') {
  const missed=status!=='met';
  return `<details class="criterion ${missed?'missed':''}" ${missed?'open':''}><summary ${missed?`data-action="focus-point" data-mp-id="${esc(mpId)}" title="Open a focused concept check"`:''}><span class="criterion-status">${icon(missed?'sparkle':'check')}</span>${esc(title)}<span class="criterion-score">${score!==null?`<b>${score}</b> / 1`:esc(status)}</span>${icon(missed?'arrow':'down','down-icon')}</summary><div class="criterion-body">${esc(reason)}${evidence?`<blockquote class="evidence-quote">“${esc(evidence)}”</blockquote>`:''}${missed?`<button class="point-tutor-link" data-action="focus-point" data-mp-id="${esc(mpId)}">Help me understand this ${icon('arrow')}</button>`:''}</div></details>`;
}

function renderMarking(q) {
  if (state.document?.purpose==='question') return `<div class="empty-panel">${icon('book')}<h3>Find the right question</h3><p>Confirm the scanned question text first, then choose a matching question and rubric.</p></div>${btn('panel-tab','Review question text','text','primary full','data-tab="text"')}`;
  const result=state.grade?.result, score=state.demo?q.score:result?.score, max=q.marks;
  if (!state.demo && result?.needs_human_review) return `<div class="empty-panel">${icon('help')}<h3>No reliable mark yet</h3><p>${esc(result.feedback||'The rubric or answer needs clarification. No score has been assigned.')}</p></div><div class="notice">Your work is saved. Start a new practice with clearer text or a complete question if needed.</div>`;
  if (!state.demo && !state.grade) return `<div class="score-topline"><span class="section-label">Question ${esc(labelOf(q))}</span><span class="badge">${max} marks</span></div><div class="empty-panel">${icon('mark')}<h3>Ready when you are</h3><p>${state.activeAnswer?'Your answer is saved. Mark it against the question’s rubric.':state.document?'Confirm the extracted text before marking your answer.':'Write your answer on the page, then send it for marking.'}</p></div>${btn('grade','Mark this answer','sparkle','primary full',state.busy?'disabled':'')}<div class="notice">The question’s scoring rules determine the mark. Your original answer and any revisions are kept separately.</div>`;
  if (!state.demo && !result) return `<div class="empty-panel">${icon(state.grade.status==='queued'?'clock':'mark')}<h3>${state.grade.status==='queued'?'Marking in progress':'Couldn’t mark this reliably'}</h3><p>${state.grade.status==='queued'?'You can return to your submissions while the answer is being marked.':'Your answer is saved. Check the model connection and try again; a failed response is never converted into a score.'}</p></div>${state.failedJob?btn('retry-job','Try marking again','arrow','full',`data-id="${state.failedJob}"`):''}`;
  const points=state.demo?q.criteria:result.point_results;
  return `<div class="score-topline"><span style="font-size:11px;font-weight:600">Question ${esc(labelOf(q))}</span><span class="badge ${score===max?'green':'amber'}">${state.demo?'Example grade':'AI feedback'}</span></div><div class="score-block"><div><div class="big-score">${score??'—'}<small>/ ${max}</small></div><div class="score-caption">${score===max?'All marks awarded':score===null?'Awaiting a response':`${Math.max(0,max-(score||0))} mark${max-score===1?'':'s'} to explore`}</div></div><div class="score-ring" style="--progress:${100*(score||0)/max}">${icon(score===max?'check':'mark')}</div></div><h3 class="section-label">${state.demo?'Marking criteria':result.scheme==='holistic'?'Assessment dimensions':'Marking-point feedback'}</h3><div class="criteria">${points.map((p,i)=>state.demo?criterion(p[0],p[1],p[2],p[2]==='met'?1:0,`MP${i+1}`):criterion(pointTitle(p),p.reason,p.status,null,p.mp_id,p.evidence_quote)).join('')}</div>${!state.demo&&result.scheme==='holistic'?'<div class="notice">The score uses best-fit levels. These dimensions identify what to work on; they are not added together as marks.</div>':''}${score!==max?`<div class="feedback-note"><div class="feedback-title">${icon('comment')}${state.demo?'A small step to improve':'Feedback'}</div><p>${state.demo?q.id==='example-3'?'You have the right idea. Make the link to the question: mention workers remaining available for work up to the age of 64.':esc(q.criteria.find(p=>p[2]!=='met')?.[1] || 'Add the next part of your explanation.'):esc(result.feedback)}</p></div>`:''}<div class="teach-card"><h3>Click a missed point to begin.</h3><p>The tutor starts with a short probe, so the guidance follows what you actually understand.</p>${btn('start-tutor','Work on this concept','sparkle','soft full',score===max?'disabled':'')}</div>${!state.demo&&result.score_basis?`<details class="rubric-copy" style="margin-top:15px"><summary class="small muted">How this score was reached</summary><p style="margin-top:8px">${esc(result.score_basis)}</p></details>`:''}`;
}

function renderTextReview(q) {
  const isQuestion=state.document?.purpose==='question';
  const value=state.demo?q.answer:state.transcript?.text ?? state.activeAnswer?.text ?? state.answerDraft;
  return `<h3 class="section-label">${isQuestion?'Confirm the question':'Review the answer text'}</h3><p class="body-copy">Compare the text with the original page. Check numbers, units and any crossed-out words.</p>${state.demo?'<span class="badge green">Example · text confirmed</span>':state.document?.ocr?'<span class="badge green">Google OCR completed</span>':''}<textarea id="transcript-text" class="text-editor" aria-label="${isQuestion?'Question':'Answer'} transcription" placeholder="Run OCR or type the text from your scan…" ${state.activeAnswer?'readonly':''}>${esc(value||'')}</textarea><div class="panel-actions">${state.document&&!state.activeAnswer?`${btn('ocr','Scan with Google OCR','scan','',state.busy?'disabled':'')}${btn('confirm-text',isQuestion?'Find matching question':'Confirm answer text','check','primary')}`:state.demo?btn('upload','Use your own answer','arrow','primary'):btn('grade',state.activeAnswer?'Mark saved answer':'Mark this answer','sparkle','primary')}</div><div class="notice">${state.document?'Select answer regions on the page to keep the question separate. Confirming creates a new text version; the original OCR stays intact.':'A confirmed transcription is the grading evidence. Empty OCR output is never automatically marked as a blank answer.'}</div>`;
}

function renderTutor(q) {
  const session=state.session;
  if (state.demo) return `<span class="badge red">Sample tutoring · ${esc(state.focusedPoint||'MP3')}</span><h3 class="section-label" style="margin-top:16px">Let’s check the concept.</h3><p class="body-copy">A lost mark can come from an incomplete explanation, rather than a missing concept.</p><div class="probe-box"><div class="eyebrow">Concept check · ${esc(q.topic)}</div>${q.id==='example-3'?'Why might a higher pension age lead more people to remain available for work?':'How would you explain the idea behind this point in your own words?'}</div><div class="lesson"><strong>What happens next?</strong>Answer a short probe, get focused guidance, then try a fresh check. Your exam mark changes only when you revise and regrade your answer.</div>${btn('upload','Try with your own answer','arrow','primary full')}<p class="body-copy" style="margin-top:13px">This is an example of the flow, not a live model conversation.</p>`;
  if (!session) return `<div class="empty-panel">${icon('sparkle')}<h3>Start with a missed point</h3><p>Click a missed marking point to open its concept check here. Your answer stays beside the conversation.</p></div>${btn('start-tutor','Start a concept check','sparkle','primary full',!state.grade?.result?'disabled':'')}`;
  return `<div class="score-topline"><h3 class="section-label">${session.focused_mp_id?`Exploring ${esc(session.focused_mp_id)}`:'Your concept checks'}</h3><span class="badge">${session.reply_count} / 6 replies</span></div>${session.evidence.slice(-2).map(e=>`<div class="chat-reply"><span>You</span><p>${multiline(e.reply)}</p><small>${esc(e.explanation)}</small></div>`).join('')}${session.messages.slice(-3).map(m=>`<div class="lesson"><strong>${m.mode==='skill_gap'?'Develop your exam explanation':m.mode==='misconception'?'Let’s revisit that idea':'A focused explanation'}</strong>${multiline(m.explanation)}${m.worked_example?`<p style="margin-top:8px">${multiline(m.worked_example)}</p>`:''}</div>`).join('')}${session.active_probe?`<div class="probe-box"><div class="eyebrow">${session.active_probe.after_teaching?'Fresh check':'Concept probe'}</div>${esc(session.active_probe.question)}</div><form id="probe-form"><textarea name="reply" class="text-editor" style="min-height:110px" placeholder="Explain in your own words…" aria-label="Your concept-check reply" maxlength="8000" required></textarea><button class="btn primary full" style="margin-top:10px" type="submit" ${state.busy?'disabled':''}>Send reply ${icon('arrow')}</button></form>`:`<div class="notice ${session.state==='processing'?'loading-dot':''}">${session.state==='processing'?'Preparing the next step…':esc(session.stop_reason||session.state.replaceAll('_',' '))}</div>`}${session.state==='awaiting_revision'?`<p class="body-copy" style="margin-top:15px">Bring that understanding back to your exam answer.</p>${btn('revise','Revise your answer','text','primary full')}`:''}${session.latest_grade_id?btn('latest-grade','View revised grade','arrow','full','style="margin-top:10px"'):''}${!['stopped','completed'].includes(session.state)?btn('stop-tutor','Finish for now','','ghost full','style="margin-top:12px"'):''}`;
}

function renderOverview() {
  const count=state.submissions.length, marked=state.submissions.reduce((n,s)=>n+s.marked,0);
  return `<div class="stats">${[[count,'Submissions','Your work in one place','folder'],[marked,'Marked answers','Feedback you can build on','check'],[state.evidence.length,'Concept checks','Evidence of understanding','sparkle']].map(([value,title,copy,symbol])=>`<div class="stat"><div><div class="eyebrow">${title}</div><strong>${value}</strong><p>${copy}</p></div><span class="stat-icon">${icon(symbol)}</span></div>`).join('')}</div><div class="table-card"><div class="table-heading"><h2>Recent submissions</h2>${btn('refresh','Refresh','clock','ghost compact')}</div>${submissionTable()}</div>`;
}

function submissionTable() {
  if (!state.submissions.length) return empty('Your first submission starts here','Upload a scan or type an answer to begin.','folder',btn('upload','Upload a response','upload','primary'));
  return `<div class="table-wrap"><table><thead><tr><th>SUBMISSION</th><th>SAVED</th><th>PROGRESS</th><th></th></tr></thead><tbody>${state.submissions.map(s=>`<tr><td><strong>${esc(s.title)}</strong><small>${s.answer_count} answer${s.answer_count===1?'':'s'}</small></td><td>${new Date(s.created_at*1000).toLocaleDateString()}</td><td><span class="badge ${s.marked?'green':'amber'}">${s.marked} / ${s.answer_count} marked</span></td><td><button class="table-action" data-action="open-submission" data-id="${s.id}">Open ${icon('arrow')}</button></td></tr>`).join('')}</tbody></table></div>`;
}

function renderBank(filter='') {
  const questions=state.questions.filter(q=>!filter||`${q.question} ${q.q_id}`.toLowerCase().includes(filter.toLowerCase()));
  return `<div class="table-heading" style="padding:0 0 18px;border:0"><span class="small muted">${state.questions.length} questions from your Economics corpus</span><input class="search-input" id="question-search" placeholder="Search questions…" aria-label="Search questions" value="${esc(filter)}"></div><div class="question-list" id="bank-list">${questions.length?questions.map(q=>`<article class="bank-card"><span class="bank-symbol">${icon('book')}</span><div class="bank-info"><h3>${esc(q.question)}</h3><p><span>${sourceOf(q)} · Q${esc(labelOf(q))}</span><span>${q.marks} marks</span><span>${q.scheme==='holistic'?'Best-fit levels':'Points scheme'}</span></p></div>${btn('choose-practice','Practise','chevron','compact',`data-id="${esc(q.id)}"`)}</article>`).join(''):empty('No matching questions','Try another search.','book')}</div>`;
}

function renderProgress() {
  if (!state.evidence.length) return empty('Understanding grows with evidence','Click a missed point in a marked answer to start a concept check. Your replies will appear here.','chart',btn('nav','Go to marking','arrow','primary','data-view="workspace"'));
  return `<div class="progress-list">${state.evidence.map(e=>`<article class="evidence-card"><span class="badge ${e.verdict==='knows'?'green':'amber'}">${esc(e.verdict.replaceAll('_',' '))}</span><h3>${esc(e.kp_id)}</h3><p>${esc(e.explanation)}</p><blockquote>${esc(e.reply)}</blockquote><p style="margin-top:13px">${e.after_teaching?'Fresh evidence after teaching':'Initial concept check'} · ${new Date(e.time*1000).toLocaleDateString()}</p></article>`).join('')}</div>`;
}

async function refreshData() {
  [state.submissions,state.evidence]=await Promise.all([api('/submissions'),api('/me/concept-evidence')]);
}

async function loadQuestions() {
  const records=[];
  for (let offset=0;offset<1000;offset+=100) {
    const page=await api(`/questions?offset=${offset}&limit=100`);records.push(...page);
    if (page.length<100) break;
  }
  state.questions=records;
}

async function loadQuestion(id) {
  const detail=await api(`/questions/${encodeURIComponent(id)}`);
  state.question={...detail,...detail.record};
}

async function loadPage() {
  if (!state.document) return;
  const id=state.document.id,page=state.page,key=`${id}:${page}`;
  if (state.pageUrls.has(key)) return;
  const response=await fetch(`/api/v1/documents/${id}/pages/${page}`);
  if (!response.ok) throw new Error('The scanned page could not be loaded.');
  state.pageUrls.set(key,URL.createObjectURL(await response.blob()));
}

async function beginPractice(id) {
  state.demo=false;resetWorkspace();
  await loadQuestion(id);
  state.view='workspace';state.tab='marking';syncWorkspaceRoute();render();
}

async function openSubmission(id) {
  state.demo=false;resetWorkspace();
  state.submission=await api(`/submissions/${id}`);
  const initial=state.submission.answers.at(-1);
  if (initial) await openAnswer(initial);
  state.view='workspace';syncWorkspaceRoute();render();
}

async function openAnswer(answer) {
  clearPageUrls();state.document=null;state.page=0;state.transcript=null;state.regions=[];state.session=null;
  await loadQuestion(answer.question_id);
  state.activeAnswer=answer;state.answerDraft=answer.text;state.grade=null;
  if (answer.grade_id) state.grade=await api(`/grades/${answer.grade_id}`);
  if (answer.document_id) {
    state.document=await api(`/documents/${answer.document_id}`);
    state.transcript=state.document.transcripts.find(t=>t.id===answer.transcript_id)||null;
    state.regions=state.transcript?.regions||[];
    await loadPage();
  }
  state.tab='marking';
}

async function pollJob(reference, update=()=>{}) {
  const id=reference.job_id || reference.id;
  for (let n=0;n<1200;n++) {
    const job=await api(`/jobs/${id}`);
    if (job.status==='succeeded') return job;
    if (job.status==='failed') {state.failedJob=id;throw new Error(job.error||'This step could not be completed. Your work is saved.');}
    await update(job);
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
  throw new Error('This is taking longer than expected. Your work is saved; reopen it from My submissions.');
}

async function task(label, work) {
  if (state.busy) return toast('Please wait for the current step to finish.');
  state.busy=label;render();
  try {await work();}
  catch(error) {toast(error.message,true);}
  finally {state.busy='';render();}
}

function showPractice() {
  modal('Start a typed answer',`<p class="body-copy">Choose an Economics question. Your answer, marking feedback and tutor will share one workspace.</p><form id="practice-form"><label class="form-field"><span>Question</span><select name="question_id" required><option value="">Choose a question…</option>${questionOptions()}</select></label><button type="submit" class="btn primary full">Open question ${icon('arrow')}</button><p class="form-error" role="alert"></p></form>`);
}

function showHelp() {
  modal('Your answer, one step at a time',`<p class="body-copy">EconReview is a local Economics practice tool. There are no accounts or teacher approval steps.</p><div class="rubric-copy"><h3>1. Upload or type</h3><p>Choose the question, then open a PDF, JPG or PNG response. You can also type an answer directly.</p></div><div class="rubric-copy"><h3>2. Confirm the text</h3><p>Use Google OCR or enter the text manually. Compare it with the original and select answer regions before confirming.</p></div><div class="rubric-copy"><h3>3. Mark and explore</h3><p>Mark the answer, then click any missed point. The tutor starts a focused concept probe, gives guidance and checks fresh evidence.</p></div><div class="notice">Google OCR and model marking need the connections described in the project README. The example workspace lets you explore the interface without sending anything to either service.</div>`);
}

async function markAnswer() {
  if (state.demo) {state.view='upload';location.hash='upload';render();return;}
  if (state.document?.purpose==='question') throw new Error('Find the matching question before adding an answer.');
  if (state.document&&!state.transcript&&!state.activeAnswer) {state.tab='text';throw new Error('Confirm the answer text before marking.');}
  await task('Marking your answer…', async()=>{
    if (!state.submission) state.submission={...await api('/submissions',{method:'POST'}),answers:[]};
    if (!state.activeAnswer) {
      const body=state.transcript?{question_id:state.question.id,transcript_id:state.transcript.id}:{question_id:state.question.id,text:state.answerDraft};
      state.activeAnswer=await api(`/submissions/${state.submission.id}/answers`,{method:'POST',body});
      state.submission.answers.push(state.activeAnswer);
      syncWorkspaceRoute();
    }
    const health=await fetch('/health').then(r=>r.json());
    if (!health.model_configured) {toast('Answer saved. The marking service is not connected yet.');await refreshData();return;}
    const queued=await api(`/answers/${state.activeAnswer.id}/grades`,{method:'POST'});
    state.grade={id:queued.grade_id,status:'queued',result:null};state.tab='marking';render();
    try {await pollJob(queued);} finally {state.grade=await api(`/grades/${queued.grade_id}`);}
    await refreshData();
    toast('Marking is ready. Click a missed point to explore it.');
  });
}

async function startTutor(mpId) {
  state.focusedPoint=mpId||null;state.tab='tutor';
  if (state.demo) {render();return;}
  if (!state.grade?.result) throw new Error('Mark an answer before starting a concept check.');
  await task('Finding a focused concept probe…',async()=>{
    const queued=await api(`/grades/${state.grade.id}/tutoring-sessions`,{method:'POST',body:mpId?{mp_id:mpId}:{}});
    state.session={id:queued.tutorial_id,state:'processing',reply_count:0,evidence:[],messages:[]};
    render();
    try {if (queued.job_id) await pollJob(queued);} finally {state.session=await api(`/tutoring-sessions/${queued.tutorial_id}`);}
  });
}

async function confirmText() {
  const text=$('#transcript-text')?.value ?? state.answerDraft;
  if (!state.document) return;
  if (!text.trim()) {
    modal('Confirm a blank answer?',`<p class="body-copy">No text is entered. If the scan contains writing, correct it before marking. Only continue if you really left this answer blank.</p>${btn('confirm-blank','This answer is blank','check','primary full')}`);
    return;
  }
  await saveTranscript(text);
}

async function saveTranscript(text) {
  await task('Saving the confirmed text…',async()=>{
    const regions=state.regions.length?state.regions:state.document.pages.map((p,page)=>({page,x:0,y:0,width:1,height:1}));
    state.transcript=await api(`/documents/${state.document.id}/transcripts`,{method:'POST',body:{text,confirmed:true,regions}});
    state.answerDraft=text;
    if (state.document.purpose==='question') {
      const matches=await api('/question-matches',{method:'POST',body:{transcript_id:state.transcript.id}});
      modal('Choose the matching question',`<p class="body-copy">Check the exact question, paper and mark total. A similar wording alone does not establish the correct rubric.</p><div class="question-list">${matches.suggestions.map(q=>`<div class="bank-card"><div class="bank-info"><h3>${esc(q.question)}</h3><p>${esc(q.q_id)}</p></div>${btn('match-question','Choose','arrow','compact',`data-id="${q.id}"`)}</div>`).join('')}</div><p class="body-copy" style="margin-top:16px">If none matches, choose the correct question from the question bank. An unknown rubric is never invented for marking.</p>`);
    } else {state.tab='marking';toast('Text confirmed. Your answer is ready to mark.');}
  });
}

async function runOCR() {
  if (!state.document || state.demo) return;
  await task('Reading the scan with Google OCR…',async()=>{
    const queued=await api(`/documents/${state.document.id}/ocr`,{method:'POST'});
    await pollJob(queued);
    state.document=await api(`/documents/${state.document.id}`);
    state.answerDraft=state.document.ocr?.text||'';state.transcript=null;
    toast(state.answerDraft?'OCR is ready. Check the text against your scan.':'No readable text was found. Type the answer manually or try a clearer scan.');
  });
}

function bindSelection() {
  const stage=$('#scan-stage');
  if (!stage || !state.selecting) return;
  let start=null,box=null;
  const point=event=>{const rect=stage.getBoundingClientRect();return {x:Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),y:Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))};};
  stage.addEventListener('pointerdown',event=>{
    if (event.button!==0) return;
    event.preventDefault();start=point(event);stage.setPointerCapture(event.pointerId);
    box=document.createElement('div');box.className='scan-region';stage.append(box);
  });
  stage.addEventListener('pointermove',event=>{
    if (!start) return;const p=point(event);
    Object.assign(box.style,{left:`${Math.min(p.x,start.x)*100}%`,top:`${Math.min(p.y,start.y)*100}%`,width:`${Math.abs(p.x-start.x)*100}%`,height:`${Math.abs(p.y-start.y)*100}%`});
  });
  stage.addEventListener('pointerup',event=>{
    if (!start) return;const p=point(event),region={page:state.page,x:Math.min(p.x,start.x),y:Math.min(p.y,start.y),width:Math.abs(p.x-start.x),height:Math.abs(p.y-start.y)};
    start=null;if (region.width>.01&&region.height>.01) {state.regions.push(region);state.transcript=null;}
    render();
  });
  stage.addEventListener('pointercancel',()=>{start=null;box?.remove();});
}

document.addEventListener('input',event=>{
  if (['answer-draft','transcript-text'].includes(event.target.id)) {state.answerDraft=event.target.value;if (state.transcript&&state.transcript.text!==state.answerDraft) state.transcript=null;}
  if (event.target.id==='question-search') {
    const fragment=document.createElement('div');fragment.innerHTML=renderBank(event.target.value);
    $('#bank-list').innerHTML=$('#bank-list',fragment).innerHTML;
  }
});

document.addEventListener('change',async event=>{
  if (event.target.id==='upload-input') $('#upload-file-label').textContent=event.target.files[0]?.name||'PDF, JPG or PNG · up to 20 MB';
  if (event.target.id==='upload-purpose') {const field=$('#upload-question-field');field.hidden=event.target.value==='question';$('select',field).required=!field.hidden;}
  if (event.target.id==='page-select') {
    state.page=Number(event.target.value);
    try {await loadPage();render();} catch(error) {toast(error.message,true);}
  }
});

document.addEventListener('submit',async event=>{
  const form=event.target;event.preventDefault();
  const data=new FormData(form);
  if (state.busy) return;
  try {
    if (form.id==='practice-form') {$('#modal').close();await beginPractice(data.get('question_id'));}
    if (form.id==='upload-form') {
      const file=data.get('file');if (!file?.size) throw new Error('Choose a scan first.');
      if (file.size>20*1024*1024) throw new Error('Choose a file smaller than 20 MB.');
      const purpose=data.get('purpose'),questionId=data.get('question_id');
      await task('Opening your scan…',async()=>{
        const body=new FormData();body.set('file',file);body.set('purpose',purpose);
        const doc=await api('/documents',{method:'POST',body});
        state.demo=false;resetWorkspace();state.document=doc;state.view='workspace';state.tab='text';
        if (purpose==='student_answer') await loadQuestion(questionId);
        else state.question={id:'question-scan',label:'?',marks:0,question:'Confirm the scanned question text to find the matching rubric.'};
        await loadPage();syncWorkspaceRoute();
      });
    }
    if (form.id==='probe-form') {
      const id=state.session.id;
      await task('Considering your reply…',async()=>{
        const queued=await api(`/tutoring-sessions/${id}/probe-replies`,{method:'POST',body:{version:state.session.version,text:data.get('reply')}});
        try {await pollJob(queued);} finally {state.session=await api(`/tutoring-sessions/${id}`);}
        await refreshData();
      });
    }
    if (form.id==='revision-form') {
      $('#modal').close();const id=state.session.id;
      await task('Marking your revised answer…',async()=>{
        const queued=await api(`/tutoring-sessions/${id}/revisions`,{method:'POST',body:{version:state.session.version,answer:{question_id:state.question.id,text:data.get('answer')}}});
        try {await pollJob(queued);} finally {state.session=await api(`/tutoring-sessions/${id}`);}
        state.grade=await api(`/grades/${queued.grade_id}`);state.tab='marking';
        await refreshData();toast('Revised feedback is ready. The original answer is preserved.');
      });
    }
  } catch(error) {const target=$('.form-error',form);if(target)target.textContent=error.message;else toast(error.message,true);}
});

document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
  const action=button.dataset.action;
  try {
    if (state.busy&&!['nav','menu','close-modal','help','panel-tab'].includes(action)) return toast('Please wait for this step to finish.');
    if (action==='nav') {state.view=button.dataset.view;location.hash=state.view;if(['overview','progress'].includes(state.view))await refreshData();render();}
    if (action==='upload') {state.view='upload';location.hash='upload';render();}
    if (action==='menu') $('.sidebar').classList.toggle('mobile-open');
    if (action==='help') showHelp();
    if (action==='close-modal') $('#modal').close();
    if (action==='new-practice') showPractice();
    if (action==='choose-practice'||action==='match-question') {$('#modal').close();await beginPractice(button.dataset.id);}
    if (action==='sample-open') {state.demo=true;resetWorkspace();state.view='workspace';location.hash='workspace';render();}
    if (action==='question') {
      if (state.demo) {state.question=demoQuestions.find(q=>q.id===button.dataset.id);state.tab='marking';render();}
      else {const answer=state.submission?.answers.filter(a=>a.question_id===button.dataset.id).at(-1);if(answer)await openAnswer(answer);else await loadQuestion(button.dataset.id);render();}
    }
    if (action==='panel-tab') {state.tab=button.dataset.tab;render();}
    if (action==='zoom-in'||action==='zoom-out') {state.zoom=Math.max(60,Math.min(150,state.zoom+(action==='zoom-in'?10:-10)));render();}
    if (action==='select-region') {if(!state.document)return toast('Upload a scan to select its answer regions.');if(state.activeAnswer)return toast('This answer is saved. Use a new practice or revision to change it.');state.selecting=!state.selecting;if(state.selecting)toast('Drag a rectangle around the answer. Add regions in reading order.');render();}
    if (action==='clear-regions') {if(state.activeAnswer)return;state.regions=[];state.transcript=null;render();}
    if (action==='ocr') await runOCR();
    if (action==='confirm-text') await confirmText();
    if (action==='confirm-blank') {$('#modal').close();await saveTranscript('');}
    if (action==='grade') await markAnswer();
    if (action==='start-tutor'||action==='focus-point') {event.preventDefault();await startTutor(button.dataset.mpId);}
    if (action==='open-submission') await openSubmission(button.dataset.id);
    if (action==='refresh') {await refreshData();render();toast('Workspace refreshed.');}
    if (action==='stop-tutor') {await api(`/tutoring-sessions/${state.session.id}/stop`,{method:'POST'});state.session=await api(`/tutoring-sessions/${state.session.id}`);render();}
    if (action==='latest-grade') {state.grade=await api(`/grades/${state.session.latest_grade_id}`);state.tab='marking';render();}
    if (action==='revise') modal('Revise your exam answer',`<p class="body-copy">Use what you have just explored. Your original answer and rubric stay unchanged.</p><form id="revision-form"><label class="form-field"><span>Your revised answer</span><textarea name="answer" required maxlength="40000" style="min-height:200px">${esc(state.answerDraft)}</textarea></label><button class="btn primary full" type="submit">Mark revised answer ${icon('arrow')}</button><p class="form-error" role="alert"></p></form>`);
    if (action==='retry-job') await task('Trying the saved step again…',async()=>{const queued=await api(`/jobs/${button.dataset.id}/retry`,{method:'POST'});try{await pollJob(queued);}finally{if(state.grade)state.grade=await api(`/grades/${state.grade.id}`);}});
  } catch(error) {toast(error.message,true);render();}
});

// Drop files onto the upload page without reading or storing them in browser storage.
document.addEventListener('dragover',event=>{if(event.target.closest('#upload-drop')){event.preventDefault();$('#upload-drop').classList.add('drag-over');}});
document.addEventListener('dragleave',event=>{if(event.target.closest('#upload-drop'))$('#upload-drop').classList.remove('drag-over');});
document.addEventListener('drop',event=>{
  if(!event.target.closest('#upload-drop'))return;event.preventDefault();
  const input=$('#upload-input');input.files=event.dataTransfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));$('#upload-drop').classList.remove('drag-over');
});
$('#modal').addEventListener('click',event=>{if(event.target===$('#modal')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
window.addEventListener('hashchange',()=>{const view=location.hash.slice(1).split('?')[0];if(viewTitles[view]&&view!==state.view){state.view=view;render();}});

async function bootstrap() {
  const [view,query='']=location.hash.slice(1).split('?');
  if(viewTitles[view])state.view=view;
  render();
  try {
    await Promise.all([loadQuestions(),refreshData()]);
    const params=new URLSearchParams(query);
    if(view==='workspace'&&params.get('submission')) await openSubmission(params.get('submission'));
    else if(view==='workspace'&&params.get('document')) {
      state.demo=false;resetWorkspace();state.view='workspace';
      state.document=await api(`/documents/${encodeURIComponent(params.get('document'))}`);
      if(params.get('question'))await loadQuestion(params.get('question'));
      else state.question={id:'question-scan',label:'?',marks:0,question:'Confirm the scanned question text to find the matching rubric.'};
      state.transcript=state.document.transcripts.at(-1)||null;
      state.regions=state.transcript?.regions||[];state.answerDraft=state.transcript?.text||state.document.ocr?.text||'';
      state.tab=state.transcript?'marking':'text';await loadPage();
    } else if(view==='workspace'&&params.get('question'))await beginPractice(params.get('question'));
    render();
  } catch(error){toast(`Could not open this local workspace: ${error.message}`,true);}
}
bootstrap();

