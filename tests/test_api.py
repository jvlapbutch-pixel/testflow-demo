import os, tempfile
from pathlib import Path
f=tempfile.TemporaryDirectory()
os.environ['TESTFLOW_DB']=str(Path(f.name)/'test.sqlite3')
from fastapi.testclient import TestClient
from app import app
c=TestClient(app)

def test_workflow():
    assert c.get('/api/health').status_code==200
    r=c.post('/api/equipments',json={'id':'tab-1','tipo':'Tablet','modelo':'T','serial':'S'});assert r.status_code==201,r.text
    assert c.post('/api/equipments',json={'id':'TAB-1','tipo':'Tablet','modelo':'T','serial':'S'}).status_code==409
    r=c.post('/api/orders',json={'equipment_id':'TAB-1','origem':'Produção','responsavel':'A','tecnico':'B'});assert r.status_code==201,r.text
    id_=r.json()['id'];assert id_=='OT-0001'
    assert c.post(f'/api/orders/{id_}/complete',json={'checks':[{'criterion':'x','result':'Aprovado'}]}).status_code==422
    assert c.post(f'/api/orders/{id_}/hardware-events',json={'device_id':'SIM','event_type':'measurement','payload':{'voltage_v':3.72}}).status_code==201
    names=c.get('/api/criteria/TAB-1').json()['criteria']
    checks=[{'criterion':n,'result':'Reprovado' if i==0 else 'Aprovado'} for i,n in enumerate(names)]
    assert c.post(f'/api/orders/{id_}/complete',json={'checks':checks}).json()['result']=='Reprovado'
    assert c.post(f'/api/orders/{id_}/complete',json={'checks':checks}).status_code==409
    assert c.post(f'/api/orders/{id_}/hardware-events',json={'device_id':'SIM','event_type':'measurement','payload':{}}).status_code==409
    assert c.post(f'/api/orders/{id_}/retest').json()['id']=='OT-0002'
    assert c.get('/api/summary').json()['failed']==1
    assert c.post('/api/movements',json={'codigo':'RES-01','descricao':'Resistor','tipo':'Saída','quantidade':1}).status_code==409
    assert c.post('/api/movements',json={'codigo':'RES-01','descricao':'Resistor','tipo':'Entrada','quantidade':10}).status_code==201
    assert c.post('/api/movements',json={'codigo':'RES-01','descricao':'Resistor','tipo':'Saída','quantidade':4,'ot_id':id_}).json()['saldo']==6
    assert c.get('/api/stock').json()[0]['saldo']==6
    assert len(c.get('/api/orders').json())==2
