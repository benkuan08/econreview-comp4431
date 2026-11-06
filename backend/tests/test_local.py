"""No-login local API; controlled providers are used only in tests."""
import copy
import io
import json
import time
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from backend.app import create_app
from backend.config import ROOT, Settings
from backend.db import Grade, Job, Question, Tutorial


class ScriptedModel:
    def __init__(self):
        self.responses, self.calls = [], []

    def complete(self, role, messages):
        self.calls.append((role, messages))
        assert self.responses, f"Unexpected model call: {role}"
        expected, value = self.responses.pop(0)
        assert role == expected
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class TestOCR:
    def extract(self, document, payload, checkpoint):
        checkpoint({"operation_name": "saved-test-operation"})
        return {"text": "The supply of labour increases.", "pages": [], "raw": {"test": True}}


@pytest.fixture
def env(tmp_path):
    model = ScriptedModel()
    settings = Settings(root=ROOT, database_url=f"sqlite:///{(tmp_path/'test.db').as_posix()}",
                        storage_dir=tmp_path/'files', worker_enabled=False, model_token="test-service")
    app = create_app(settings, model, TestOCR())
    with TestClient(app) as client:
        yield client, app, model


def question(env, holistic=False):
    client, app, _ = env
    search = "2025-06-q2g" if holistic else "2025-06-q1h"
    q = client.get(f"/api/v1/questions?search={search}").json()[0]
    with app.state.sessions() as db:
        return {**q, "record": copy.deepcopy(db.get(Question,q['id']).record)}


def result(q, text="The supply of labour increases.", score=1):
    return {"q_id":q['q_id'], "score":score, "max_score":q['marks'], "scheme":q['scheme'],
        "point_results":[{"mp_id":p['mp_id'], "status":"met" if i==0 else "unmet", "evidence_quote":text if i==0 else "", "reason":"Test evidence"} for i,p in enumerate(q['record']['marking_points'])],
        "score_basis":"Question-specific rules", "feedback":"Develop the explanation", "needs_human_review":False}


def submit(env,q,text="The supply of labour increases."):
    client,app,_=env
    sub=client.post('/api/v1/submissions').json()
    response=client.post(f"/api/v1/submissions/{sub['id']}/answers",json={"question_id":q['id'],"text":text})
    assert response.status_code==201,response.text
    answer=response.json()
    queued=client.post(f"/api/v1/answers/{answer['id']}/grades")
    assert queued.status_code==202,queued.text
    return sub,answer,queued.json()


def session_start(env,q,mp_id='MP2'):
    client,app,model=env
    model.responses.append(('grader',result(q)))
    _,_,queued=submit(env,q)
    app.state.worker.run_once()
    kp=next(p['kp_id'] for p in q['record']['marking_points'] if p['mp_id']==mp_id)
    model.responses += [('diagnoser',{"q_id":q['q_id'],"findings":[],"needs_more_evidence":True}),
        ('prober',{"kp_id":kp,"probe_index":0,"question":app.state.catalog.graph[kp]['probes'][0]['q'],"stop_reason":None})]
    response=client.post(f"/api/v1/grades/{queued['grade_id']}/tutoring-sessions",json={"mp_id":mp_id})
    assert response.status_code==202,response.text
    app.state.worker.run_once()
    return response.json()['tutorial_id'],kp,queued['grade_id']


def image_bytes():
    buf=io.BytesIO();Image.new('RGB',(100,100),'white').save(buf,'PNG');return buf.getvalue()


def test_no_login_and_questions_ready(env):
    client,app,_=env
    assert client.get('/health').json()['mode']=='local'
    paths=client.get('/openapi.json').json()['paths']
    assert not any('auth' in p or 'decisions' in p or 'assignments' in p for p in paths)
    with app.state.sessions() as db:
        qs=list(db.scalars(select(Question)))
        assert len(qs)==272 and sum(q.scheme=='holistic' for q in qs)==103
        assert all(q.approved for q in qs)
    q=question(env)
    assert 'accept' not in client.get(f"/api/v1/questions/{q['id']}").json()['record']


def test_remote_origins_and_hosts_blocked(env):
    client,_,_=env
    assert client.post('/api/v1/submissions',headers={'Origin':'https://other.example'}).status_code==403
    assert client.post('/api/v1/submissions',headers={'Origin':'http://testserver'}).status_code==201
    assert client.get('/health',headers={'Host':'attacker.example'}).status_code==400


def test_static_frontend(env):
    client,_,_=env
    assert 'type="module"' in client.get('/').text
    assert client.get('/app.js').status_code==200
    assert client.get('/styles.css').status_code==200
    assert '<svg' in client.get('/assets/polyu-logo.svg').text[:2000]


def test_grading_snapshot_idempotence_and_history(env):
    client,app,model=env;q=question(env)
    model.responses.append(('grader',result(q)))
    sub,answer,queued=submit(env,q)
    assert client.post(f"/api/v1/answers/{answer['id']}/grades").json()==queued
    assert app.state.worker.run_once() and not app.state.worker.run_once()
    grade=client.get(f"/api/v1/grades/{queued['grade_id']}").json()
    assert grade['result']['score']==1 and grade['status']=='provisional'
    assert len(model.calls)==1
    assert client.get('/api/v1/submissions').json()[0]['marked']==1
    assert client.get(f"/api/v1/submissions/{sub['id']}").json()['answers'][0]['grade_id']==grade['id']
    with app.state.sessions() as db:
        assert db.get(Grade,grade['id']).snapshot['graph_hash']==app.state.catalog.graph_hash


def test_invalid_evidence_never_becomes_a_score(env):
    client,app,model=env;q=question(env)
    invalid=result(q,'Invented student evidence')
    model.responses += [('grader',invalid),('grader',invalid)]
    _,_,queued=submit(env,q);app.state.worker.run_once()
    grade=client.get(f"/api/v1/grades/{queued['grade_id']}").json()
    assert grade['result'] is None and grade['status']=='needs_review'
    assert client.get(queued['status_url']).json()['status']=='failed' and len(model.calls)==2


def test_failed_grade_retry(env):
    client,app,model=env;q=question(env)
    model.responses.append(('grader',RuntimeError('No connection')))
    _,_,queued=submit(env,q);app.state.worker.run_once()
    assert client.get(queued['status_url']).json()['status']=='failed'
    model.responses.append(('grader',result(q)))
    assert client.post(f"/api/v1/jobs/{queued['job_id']}/retry").status_code==202
    app.state.worker.run_once()
    assert client.get(f"/api/v1/grades/{queued['grade_id']}").json()['result']['score']==1


def test_strict_score_repair(env):
    client,app,model=env;q=question(env);valid=result(q)
    model.responses += [('grader',{**valid,'score':'1'}),('grader',valid)]
    _,_,queued=submit(env,q);app.state.worker.run_once()
    assert client.get(f"/api/v1/grades/{queued['grade_id']}").json()['result']['score']==1
    assert len(model.calls)==2


def test_scan_confirmation_and_marking(env):
    client,app,model=env;q=question(env)
    uploaded=client.post('/api/v1/documents',files={'file':('answer.png',image_bytes(),'image/png')})
    assert uploaded.status_code==201,uploaded.text
    doc=uploaded.json()
    assert client.get(f"/api/v1/documents/{doc['id']}/pages/0").status_code==200
    client.post(f"/api/v1/documents/{doc['id']}/ocr");app.state.worker.run_once()
    detail=client.get(f"/api/v1/documents/{doc['id']}").json()
    assert 'raw' not in detail['ocr'] and 'operation_name' not in detail['ocr']
    transcript=client.post(f"/api/v1/documents/{doc['id']}/transcripts",json={'text':detail['ocr']['text'],'confirmed':True,'regions':[{'page':0,'x':0,'y':0,'width':1,'height':1}]}).json()
    sub=client.post('/api/v1/submissions').json()
    answer=client.post(f"/api/v1/submissions/{sub['id']}/answers",json={'question_id':q['id'],'transcript_id':transcript['id']}).json()
    model.responses.append(('grader',result(q)))
    grade=client.post(f"/api/v1/answers/{answer['id']}/grades").json();app.state.worker.run_once()
    assert client.get(f"/api/v1/grades/{grade['grade_id']}").json()['result']['score']==1


def test_pdf_and_invalid_regions(env):
    import pymupdf
    client,_,_=env
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30,30),'Question');pdf.new_page().insert_text((30,30),'Answer');content=pdf.tobytes()
    response=client.post('/api/v1/documents',files={'file':('scan.pdf',content,'application/pdf')})
    assert len(response.json()['pages'])==2
    doc=response.json()['id']
    assert client.post(f'/api/v1/documents/{doc}/transcripts',json={'text':'X','confirmed':True,'regions':[{'page':2,'x':0,'y':0,'width':1,'height':1}]}).status_code==422
    assert client.post('/api/v1/documents',files={'file':('fake.png',b'bad','image/png')}).status_code==422


def test_clicking_specific_missed_point(env):
    client,app,model=env;q=question(env)
    identifier,kp,grade_id=session_start(env,q,'MP3')
    session=client.get(f'/api/v1/tutoring-sessions/{identifier}').json()
    assert session['focused_mp_id']=='MP3' and session['active_probe']['kp_id']==kp
    assert 'answer' not in session['active_probe']
    for mp_id in ['MP1','invented']:
        assert client.post(f'/api/v1/grades/{grade_id}/tutoring-sessions',json={'mp_id':mp_id}).status_code==422


def test_duplicate_reply_and_stop(env):
    client,app,_=env;identifier,kp,_=session_start(env,question(env))
    session=client.get(f'/api/v1/tutoring-sessions/{identifier}').json()
    body={'version':session['version'],'text':'Not sure'}
    assert client.post(f'/api/v1/tutoring-sessions/{identifier}/probe-replies',json=body).status_code==202
    assert client.post(f'/api/v1/tutoring-sessions/{identifier}/probe-replies',json=body).status_code==409
    client.post(f'/api/v1/tutoring-sessions/{identifier}/stop');app.state.worker.run_once()
    assert client.get(f'/api/v1/tutoring-sessions/{identifier}').json()['state']=='stopped'


def test_fresh_evidence_after_teaching(env):
    client,app,model=env;identifier,kp,grade_id=session_start(env,question(env))
    session=client.get(f'/api/v1/tutoring-sessions/{identifier}').json()
    model.responses += [('probe_judge',{'kp_id':kp,'verdict':'knows','evidence_quote':'available','explanation':'Concept understood','follow_up_needed':False}),
        ('tutor',{'kp_id':kp,'mode':'skill_gap','explanation':'Apply the concept','worked_example':None,'check_question':'Unvalidated check','source_ids':[]}),
        ('prober',{'kp_id':kp,'probe_index':1,'question':app.state.catalog.graph[kp]['probes'][1]['q'],'stop_reason':None})]
    client.post(f'/api/v1/tutoring-sessions/{identifier}/probe-replies',json={'version':session['version'],'text':'People available for work'});app.state.worker.run_once()
    session=client.get(f'/api/v1/tutoring-sessions/{identifier}').json()
    assert session['active_probe']['after_teaching'] and not session['evidence'][0]['after_teaching']
    assert 'Unvalidated check' not in json.dumps(session)
    assert client.get(f'/api/v1/grades/{grade_id}').json()['result']['score']==1


def test_changing_focus_preserves_budget(env):
    client,app,model=env;q=question(env);identifier,kp,grade_id=session_start(env,q)
    with app.state.sessions() as db:
        tutorial=db.get(Tutorial,identifier);data=copy.deepcopy(tutorial.data);data['reply_count']=4;tutorial.data=data;db.commit()
    model.responses += [('diagnoser',{'q_id':q['q_id'],'findings':[],'needs_more_evidence':True}),
        ('prober',{'kp_id':kp,'probe_index':1,'question':app.state.catalog.graph[kp]['probes'][1]['q'],'stop_reason':None})]
    assert client.post(f'/api/v1/grades/{grade_id}/tutoring-sessions',json={'mp_id':'MP3'}).json()['tutorial_id']==identifier
    app.state.worker.run_once();session=client.get(f'/api/v1/tutoring-sessions/{identifier}').json()
    assert session['reply_count']==4 and session['focused_mp_id']=='MP3'


def test_two_revisions_no_budget_reset(env):
    client,app,model=env;q=question(env);identifier,kp,_=session_start(env,q)
    with app.state.sessions() as db:
        db.get(Tutorial,identifier).state='awaiting_revision';db.commit()
    for n in range(2):
        session=client.get(f'/api/v1/tutoring-sessions/{identifier}').json();text=f'Revision {n}'
        model.responses.append(('grader',result(q,text)))
        response=client.post(f'/api/v1/tutoring-sessions/{identifier}/revisions',json={'version':session['version'],'answer':{'question_id':q['id'],'text':text}})
        assert response.status_code==202,response.text
        app.state.worker.run_once()
        assert client.post(f"/api/v1/grades/{response.json()['grade_id']}/tutoring-sessions").status_code==409
    session=client.get(f'/api/v1/tutoring-sessions/{identifier}').json()
    assert session['state']=='completed' and session['revisions']==2


def test_holistic_is_not_point_counting(env):
    client,app,model=env;q=question(env,True);score=q['marks']-1
    model.responses.append(('grader',result(q,score=score)));_,_,queued=submit(env,q);app.state.worker.run_once()
    assert client.get(f"/api/v1/grades/{queued['grade_id']}").json()['result']['score']==score


def test_expired_job_lease(env):
    client,app,model=env;q=question(env);model.responses.append(('grader',result(q)));_,_,queued=submit(env,q)
    with app.state.sessions() as db:
        job=db.get(Job,queued['job_id']);job.status,job.lease,job.lease_until='running','old',time.time()-1;db.commit()
    app.state.worker.run_once();assert client.get(queued['status_url']).json()['status']=='succeeded'
