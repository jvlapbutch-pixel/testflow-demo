"""TestFlow Demo v0.2 — local, single-user MVP. Run: uvicorn app:app --reload"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json, os, sqlite3, csv, io
from typing import Literal
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

BASE = Path(__file__).resolve().parent
DB = Path(os.getenv('TESTFLOW_DB', str(BASE / 'data' / 'testflow.sqlite3')))
DB.parent.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='Sistema Teste de Hardware API', version='0.5.0')
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')

def utc(): return datetime.now(timezone.utc).isoformat()

@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally: db.close()

def init():
    with connect() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS equipments (id TEXT PRIMARY KEY, tipo TEXT NOT NULL, modelo TEXT NOT NULL, serial TEXT NOT NULL, notas TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, equipment_id TEXT NOT NULL REFERENCES equipments(id), origem TEXT NOT NULL, responsavel TEXT NOT NULL, tecnico TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Em aberto', result TEXT NOT NULL DEFAULT 'Em aberto', started TEXT NOT NULL, ended TEXT, notes TEXT NOT NULL DEFAULT '', parent_id INTEGER REFERENCES orders(id));
        CREATE TABLE IF NOT EXISTS checks (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id), criterion TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, type TEXT NOT NULL, ref TEXT NOT NULL, details TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS movements (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, codigo TEXT NOT NULL, descricao TEXT NOT NULL, tipo TEXT NOT NULL, quantidade INTEGER NOT NULL, delta INTEGER NOT NULL, ot_id INTEGER REFERENCES orders(id), motivo TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS hardware_events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, order_id INTEGER NOT NULL REFERENCES orders(id), device_id TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL);
        ''')
init()

def init_catalog():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS item_metadata (
          equipment_id TEXT PRIMARY KEY REFERENCES equipments(id),
          tipo_item TEXT NOT NULL CHECK(tipo_item IN ('simples','composto')),
          possui_id INTEGER NOT NULL, id_serial TEXT,
          possui_patrimonio INTEGER NOT NULL, patrimonio TEXT,
          parent_id TEXT REFERENCES equipments(id),
          funcao_no_conjunto TEXT, observacao TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_item_parent ON item_metadata(parent_id);
        CREATE INDEX IF NOT EXISTS idx_item_serial ON item_metadata(id_serial);
        CREATE INDEX IF NOT EXISTS idx_item_patrimonio ON item_metadata(patrimonio);
        ''')
init_catalog()

def log(db, type_, ref, details):
    db.execute('INSERT INTO events(at,type,ref,details) VALUES (?,?,?,?)', (utc(),type_,str(ref),details))

def oid(i): return f'OT-{i:04d}'

def parse_ot(s):
    try:
        if not s.startswith('OT-'): raise ValueError
        return int(s[3:])
    except (ValueError,AttributeError): raise HTTPException(400, 'Código de OT inválido')

def get_order(db, id_):
    row = db.execute('SELECT * FROM orders WHERE id=?',(id_,)).fetchone()
    if not row: raise HTTPException(404,'OT não encontrada')
    return row

def order_dict(db, row):
    x=dict(row); x['id']=oid(x['id']); x['parent_id']=oid(x['parent_id']) if x['parent_id'] else None
    x['checks']=[dict(r) for r in db.execute('SELECT criterion,result FROM checks WHERE order_id=? ORDER BY id',(row['id'],))]
    return x

class EquipmentIn(BaseModel):
    id: str = Field(min_length=1,max_length=40)
    tipo: Literal['Tablet','Scanner','Monitoramento','TABSTER','Modem','Chip','Raspberry Pi','Câmera','Outro']
    modelo: str = Field(min_length=1,max_length=100)
    serial: str = Field(min_length=1,max_length=100)
    notas: str = Field(default='',max_length=1000)
    @field_validator('id')
    @classmethod
    def upper(cls,v): return v.strip().upper()

class OrderIn(BaseModel):
    equipment_id: str
    origem: str = Field(min_length=1,max_length=100)
    responsavel: str = Field(min_length=1,max_length=100)
    tecnico: str = Field(min_length=1,max_length=100)

class CheckIn(BaseModel):
    criterion: str = Field(min_length=1,max_length=200)
    result: Literal['Aprovado','Reprovado','Não aplicável']

class CompleteIn(BaseModel):
    checks: list[CheckIn] = Field(min_length=1,max_length=50)
    notes: str = Field(default='',max_length=4000)

class MovementIn(BaseModel):
    codigo: str = Field(min_length=1,max_length=60)
    descricao: str = Field(min_length=1,max_length=200)
    tipo: Literal['Entrada','Saída','Ajuste positivo','Ajuste negativo']
    quantidade: int = Field(ge=1,le=1000000)
    ot_id: str | None = None
    motivo: str = Field(default='',max_length=500)

class HardwareIn(BaseModel):
    device_id: str = Field(min_length=1,max_length=100)
    event_type: Literal['measurement','device_status','test_progress']
    payload: dict = Field(default_factory=dict)

CRITERIA = {'TABSTER':['Bateria','Scanner','Tablet'],'Tablet':['Tela ligada / duração','Brilho da tela','Funcionamento do scanner acoplado','Softwares locais','Alimentação / bateria'],'Scanner':['Leitura de código','Comunicação com tablet','Integridade física'],'Monitoramento':['Teste Curto','Leds/Display','Conexão Cameras','Foto Cameras','Aula'],'Modem':['Inicialização','Conectividade','Sinal'],'Chip':['Reconhecimento','Comunicação'],'Raspberry Pi':['Inicialização','Interfaces','Alimentação'],'Câmera':['Alimentação','Imagem','Comunicação'],'Outro':['Inspeção funcional']}

@app.get('/')
def home(): return FileResponse(BASE/'static'/'index.html', headers={'Cache-Control':'no-store, max-age=0'})

@app.get('/api/health')
def health(): return {'status':'ok','version':'0.5.0','storage':'sqlite'}

@app.get('/api/equipments')
def equipments():
    with connect() as db: return [dict(r) for r in db.execute('SELECT e.*,m.tipo_item,m.possui_id,m.id_serial,m.possui_patrimonio,m.patrimonio,m.parent_id,m.funcao_no_conjunto,m.observacao FROM equipments e LEFT JOIN item_metadata m ON m.equipment_id=e.id ORDER BY e.created_at DESC')]

@app.post('/api/equipments',status_code=201)
def add_equipment(v:EquipmentIn):
    with connect() as db:
        try: db.execute('INSERT INTO equipments VALUES (?,?,?,?,?,?)',(v.id,v.tipo,v.modelo,v.serial,v.notas,utc()))
        except sqlite3.IntegrityError: raise HTTPException(409,'Patrimônio já cadastrado')
        db.execute('INSERT INTO item_metadata(equipment_id,tipo_item,possui_id,id_serial,possui_patrimonio,patrimonio) VALUES (?,?,?,?,?,?)', (v.id,'simples',1,v.serial,0,None))
        log(db,'Equipamento cadastrado',v.id,v.tipo+' / '+v.modelo)
    return {'id':v.id}

@app.get('/api/criteria/{equipment_id}')
def criteria(equipment_id:str):
    with connect() as db:
        r=db.execute('SELECT tipo FROM equipments WHERE id=?',(equipment_id,)).fetchone()
        if not r: raise HTTPException(404,'Equipamento não encontrado')
        return {'criteria':CRITERIA[r['tipo']]}

@app.get('/api/orders')
def orders():
    with connect() as db: return [order_dict(db,r) for r in db.execute('SELECT * FROM orders ORDER BY id DESC')]

@app.post('/api/orders',status_code=201)
def add_order(v:OrderIn):
    with connect() as db:
        if not db.execute('SELECT 1 FROM equipments WHERE id=?',(v.equipment_id,)).fetchone(): raise HTTPException(404,'Equipamento não encontrado')
        if db.execute('SELECT 1 FROM commercial_allocations WHERE kit_id=? AND shipped_at IS NOT NULL AND unlinked_at IS NULL',(v.equipment_id,)).fetchone(): raise HTTPException(409,'Kit já expedido; registre retorno antes de testar novamente')
        cur=db.execute('INSERT INTO orders(equipment_id,origem,responsavel,tecnico,started) VALUES (?,?,?,?,?)',(v.equipment_id,v.origem,v.responsavel,v.tecnico,utc()))
        id_=oid(cur.lastrowid); log(db,'OT aberta',id_,v.equipment_id)
        return {'id':id_}

@app.post('/api/orders/{order_id}/complete')
def complete(order_id:str,v:CompleteIn):
    id_=parse_ot(order_id)
    with connect() as db:
        row=get_order(db,id_)
        if row['status']!='Em aberto': raise HTTPException(409,'OT já concluída')
        expected=CRITERIA[db.execute('SELECT tipo FROM equipments WHERE id=?',(row['equipment_id'],)).fetchone()['tipo']]
        names=[c.criterion for c in v.checks]
        if len(names)!=len(expected) or set(names)!=set(expected) or len(set(names))!=len(names): raise HTTPException(422,'Checklist incompleto ou incompatível com equipamento')
        result='Reprovado' if any(c.result=='Reprovado' for c in v.checks) else 'Aprovado'
        db.executemany('INSERT INTO checks(order_id,criterion,result) VALUES (?,?,?)',[(id_,c.criterion,c.result) for c in v.checks])
        db.execute("UPDATE orders SET status='Concluída',result=?,ended=?,notes=? WHERE id=?",(result,utc(),v.notes,id_))
        if result == 'Reprovado':
            db.execute("INSERT INTO reworks(kit_id,order_id,criterion,status,opened_at,notes) VALUES (?,?,?,?,?,?)", (row['equipment_id'], id_, ', '.join(c.criterion for c in v.checks if c.result=='Reprovado'), 'Na manutenção',utc(),v.notes))
        else:
            db.execute("UPDATE reworks SET status='Concluído', closed_at=?, retest_order_id=? WHERE kit_id=? AND status!='Concluído'", (utc(),id_,row['equipment_id']))
        log(db,'Teste concluído',order_id,result)
        return {'id':order_id,'result':result}

@app.post('/api/orders/{order_id}/retest',status_code=201)
def retest(order_id:str):
    id_=parse_ot(order_id)
    with connect() as db:
        row=get_order(db,id_)
        if row['status']!='Concluída': raise HTTPException(409,'Conclua a OT antes do reteste')
        cur=db.execute('INSERT INTO orders(equipment_id,origem,responsavel,tecnico,started,parent_id) VALUES (?,?,?,?,?,?)',(row['equipment_id'],row['origem'],row['responsavel'],row['tecnico'],utc(),id_))
        new=oid(cur.lastrowid);log(db,'Reteste aberto',new,'Referência: '+order_id)
        return {'id':new}

@app.get('/api/events')
def events():
    with connect() as db: return [dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 1000')]

@app.get('/api/movements')
def movements():
    with connect() as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM movements ORDER BY id DESC')]
        for r in rows: r['ot_id']=oid(r['ot_id']) if r['ot_id'] else None
        return rows

@app.get('/api/stock')
def stock():
    with connect() as db: return [dict(r) for r in db.execute('SELECT codigo,MAX(descricao) descricao,SUM(delta) saldo FROM movements GROUP BY codigo ORDER BY codigo')]

@app.post('/api/movements',status_code=201)
def add_movement(v:MovementIn):
    code=v.codigo.strip().upper(); ot=parse_ot(v.ot_id) if v.ot_id else None
    delta=v.quantidade if v.tipo in ('Entrada','Ajuste positivo') else -v.quantidade
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if ot is not None: get_order(db,ot)
        saldo=db.execute('SELECT COALESCE(SUM(delta),0) s FROM movements WHERE codigo=?',(code,)).fetchone()['s']
        if saldo+delta<0: raise HTTPException(409,'Saldo insuficiente')
        db.execute('INSERT INTO movements(at,codigo,descricao,tipo,quantidade,delta,ot_id,motivo) VALUES (?,?,?,?,?,?,?,?)',(utc(),code,v.descricao,v.tipo,v.quantidade,delta,ot,v.motivo))
        log(db,'Movimentação estoque',code,f'{v.tipo}: {v.quantidade}')
    return {'codigo':code,'saldo':saldo+delta}

@app.post('/api/orders/{order_id}/hardware-events',status_code=201)
def hardware_event(order_id:str,v:HardwareIn):
    id_=parse_ot(order_id)
    if len(json.dumps(v.payload))>16000: raise HTTPException(413,'Payload excede 16 KB')
    with connect() as db:
        row=get_order(db,id_)
        if row['status']!='Em aberto': raise HTTPException(409,'OT não está aberta')
        cur=db.execute('INSERT INTO hardware_events(at,order_id,device_id,event_type,payload_json) VALUES (?,?,?,?,?)',(utc(),id_,v.device_id,v.event_type,json.dumps(v.payload,ensure_ascii=False)))
        log(db,'Evento de hardware',order_id,f'{v.device_id}: {v.event_type}')
        return {'id':cur.lastrowid,'stored':True,'mode':'simulation/manual'}

@app.get('/api/orders/{order_id}/hardware-events')
def hardware_events(order_id:str):
    id_=parse_ot(order_id)
    with connect() as db:
        get_order(db,id_)
        rows=[dict(r) for r in db.execute('SELECT * FROM hardware_events WHERE order_id=? ORDER BY id',(id_,))]
        for r in rows: r['payload']=json.loads(r.pop('payload_json'))
        return rows

@app.get('/api/summary')
def summary():
    with connect() as db:
        equipment=db.execute('SELECT COUNT(*) n FROM equipments').fetchone()['n']
        open_=db.execute("SELECT COUNT(*) n FROM orders WHERE status='Em aberto'").fetchone()['n']
        approved=db.execute("SELECT COUNT(*) n FROM orders WHERE result='Aprovado'").fetchone()['n']
        failed=db.execute("SELECT COUNT(*) n FROM orders WHERE result='Reprovado'").fetchone()['n']
        return {'equipments':equipment,'open':open_,'approved':approved,'failed':failed,'approval_rate':round(100*approved/(approved+failed)) if approved+failed else 0}


CSV_FIELDS = ['codigo_importacao','descricao','tipo_item','possui_id','id_serial','possui_patrimonio','patrimonio','codigo_item_pai','funcao_no_conjunto','observacao']

def equipment_type(row):
    text=(row['descricao']+' '+row['codigo_importacao']).casefold()
    if row['tipo_item']=='composto':
        return 'Monitoramento' if ('monitoramento' in text or row['codigo_importacao'].startswith('MON')) else ('TABSTER' if 'tabster' in text else 'Outro')
    if 'tablet' in text or row['codigo_importacao'].startswith('TAB-'): return 'Tablet'
    if 'scanner' in text or 'fs80h' in text: return 'Scanner'
    if 'câmera' in text or 'camera' in text: return 'Câmera'
    if 'controladora' in text: return 'Outro'
    return 'Outro'

def parse_import(data):
    try: content=data.decode('utf-8-sig')
    except UnicodeDecodeError: raise HTTPException(422,'CSV deve estar em UTF-8')
    reader=csv.DictReader(io.StringIO(content),delimiter=';')
    if reader.fieldnames is None or any(c not in reader.fieldnames for c in CSV_FIELDS):
        raise HTTPException(422,'Cabeçalhos incompatíveis. Esperado: '+', '.join(CSV_FIELDS))
    rows=[]; seen=set(); serials=set()
    for n,raw in enumerate(reader,2):
        if None in raw: raise HTTPException(422,f'Linha {n}: colunas excedentes')
        x={k:(raw.get(k) or '').strip() for k in CSV_FIELDS}
        code=x['codigo_importacao'].upper()
        if not code or len(code)>40 or code in seen: raise HTTPException(422,f'Linha {n}: código ausente, repetido ou longo')
        seen.add(code); x['codigo_importacao']=code
        if x['tipo_item'] not in ('simples','composto') or not x['descricao'] or len(x['descricao'])>100: raise HTTPException(422,f'Linha {n}: descrição/tipo inválidos')
        if x['possui_id'] not in ('0','1') or x['possui_patrimonio'] not in ('0','1'): raise HTTPException(422,f'Linha {n}: flags devem ser 0 ou 1')
        if bool(x['id_serial'])!= (x['possui_id']=='1') or bool(x['patrimonio'])!=(x['possui_patrimonio']=='1'):
            raise HTTPException(422,f'Linha {n}: flags não correspondem aos valores de ID/patrimônio')
        if x['id_serial']:
            if x['id_serial'] in serials: raise HTTPException(422,f'Linha {n}: serial repetido')
            serials.add(x['id_serial'])
        if x['codigo_item_pai']: x['codigo_item_pai']=x['codigo_item_pai'].upper()
        if x['codigo_item_pai']==code: raise HTTPException(422,f'Linha {n}: item não pode ser seu próprio pai')
        rows.append(x)
    if not rows: raise HTTPException(422,'CSV vazio')
    if len(rows)>1000: raise HTTPException(422,'Limite de 1000 linhas')
    bycode={x['codigo_importacao']:x for x in rows}
    for x in rows:
        parent=x['codigo_item_pai']
        if parent and parent in bycode and bycode[parent]['tipo_item']!='composto':
            raise HTTPException(422,f'{parent} não é composto')
    return rows

@app.post('/api/import/equipments-csv')
async def import_equipments_csv(file:UploadFile=File(...)):
    if not (file.filename or '').lower().endswith('.csv'):
        raise HTTPException(422,'Envie um arquivo .csv')
    data=await file.read(1024*1024+1)
    if len(data)>1024*1024: raise HTTPException(413,'CSV maior que 1 MB')
    rows=parse_import(data)
    now=utc(); warnings=[]; inserted=[]; skipped=[]
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing={r['id']:r for r in db.execute('SELECT * FROM equipments')}
        existing_meta={r['equipment_id']:r for r in db.execute('SELECT * FROM item_metadata')}
        codes={x['codigo_importacao'] for x in rows}
        for x in rows:
            parent=x['codigo_item_pai']
            if parent and parent not in codes and parent not in existing:
                raise HTTPException(422,f'Pai não encontrado: {parent}')
            if parent in existing_meta and existing_meta[parent]['tipo_item']!='composto':
                raise HTTPException(422,f'Pai existente não é composto: {parent}')
            if x['id_serial']:
                other=db.execute('SELECT equipment_id FROM item_metadata WHERE id_serial=? AND equipment_id!=?',(x['id_serial'],x['codigo_importacao'])).fetchone()
                if other: raise HTTPException(409,f'Serial {x["id_serial"]} já pertence a {other[0]}')
            code=x['codigo_importacao']
            if code in existing:
                m=existing_meta.get(code)
                if m and m['id_serial']==(x['id_serial'] or None) and m['patrimonio']==(x['patrimonio'] or None) and m['parent_id']==(parent or None) and m['tipo_item']==x['tipo_item']:
                    skipped.append(code); continue
                raise HTTPException(409,f'Código {code} já cadastrado com dados diferentes; nenhuma alteração realizada')
        # Inserção em duas fases permite relacionamentos pai-filho independentemente da ordem no CSV.
        for x in rows:
            code=x['codigo_importacao']
            if code in skipped: continue
            notes='[IMPORTADO CSV] '+x['observacao']
            db.execute('INSERT INTO equipments(id,tipo,modelo,serial,notas,created_at) VALUES (?,?,?,?,?,?)',
                (code,equipment_type(x),x['descricao'],x['id_serial'] or '[SEM SERIAL]',notes,now))
            inserted.append(code)
        for x in rows:
            code=x['codigo_importacao']
            if code in skipped: continue
            db.execute('''INSERT INTO item_metadata
                (equipment_id,tipo_item,possui_id,id_serial,possui_patrimonio,patrimonio,parent_id,funcao_no_conjunto,observacao)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (code,x['tipo_item'],int(x['possui_id']),x['id_serial'] or None,int(x['possui_patrimonio']),x['patrimonio'] or None,
                 x['codigo_item_pai'] or None,x['funcao_no_conjunto'] or None,x['observacao']))
            if x['patrimonio']:
                others=db.execute('SELECT equipment_id FROM item_metadata WHERE patrimonio=? AND equipment_id!=?',(x['patrimonio'],code)).fetchall()
                if others: warnings.append(f'Patrimônio {x["patrimonio"]} reutilizado: {code} / '+', '.join(o[0] for o in others))
            log(db,'Item importado',code,'CSV / '+x['tipo_item'])
    return {'total_csv':len(rows),'imported':len(inserted),'skipped':len(skipped),'codes_imported':inserted,'codes_skipped':skipped,'warnings':list(dict.fromkeys(warnings))}


# v0.4 — Kits, componentes, disponibilidade e substituicoes auditaveis
class ItemIn(BaseModel):
    categoria: Literal['kit','componente']
    tipo: Literal['Monitoramento','TABSTER','Tablet','Scanner','Câmera','Outro']
    modelo: str = Field(min_length=1,max_length=100)
    id_serial: str | None = Field(default=None,max_length=100)
    patrimonio: str | None = Field(default=None,max_length=100)
    parent_id: str | None = None
    funcao: str | None = Field(default=None,max_length=100)
    notas: str = Field(default='',max_length=1000)
    codigo: str | None = Field(default=None,max_length=40)

class LinkIn(BaseModel):
    parent_id: str | None = None
    funcao: str | None = None

class SwapIn(BaseModel):
    kit_id: str
    defeituoso_id: str
    substituto_id: str
    responsavel: str = Field(min_length=1,max_length=100)
    motivo: str = Field(min_length=1,max_length=500)
    ot_id: str | None = None

class LocationIn(BaseModel):
    local: Literal['estoque','manutencao','descartado']
    motivo: str = Field(min_length=1,max_length=500)


def init_v04():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS component_locations (equipment_id TEXT PRIMARY KEY REFERENCES equipments(id),local TEXT NOT NULL DEFAULT 'estoque');
        CREATE TABLE IF NOT EXISTS component_history (id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,component_id TEXT NOT NULL REFERENCES equipments(id),kit_id TEXT REFERENCES equipments(id),action TEXT NOT NULL,details TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS kit_swaps (id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,kit_id TEXT NOT NULL,old_component TEXT NOT NULL,new_component TEXT NOT NULL,responsavel TEXT NOT NULL,motivo TEXT NOT NULL,ot_id INTEGER REFERENCES orders(id));
        """)
        # Preserve existing imported compound/simple metadata; infer category for older manual records.
        db.execute("""INSERT OR IGNORE INTO item_metadata(equipment_id,tipo_item,possui_id,id_serial,possui_patrimonio,patrimonio,parent_id)
          SELECT id, CASE WHEN tipo IN ('Monitoramento','TABSTER') THEN 'composto' ELSE 'simples' END,
          CASE WHEN serial='[SEM SERIAL]' THEN 0 ELSE 1 END,
          CASE WHEN serial='[SEM SERIAL]' THEN NULL ELSE serial END,0,NULL,NULL FROM equipments""")
        db.execute("""INSERT OR IGNORE INTO component_locations(equipment_id,local)
          SELECT e.id,CASE WHEN m.parent_id IS NOT NULL THEN 'instalado' ELSE 'estoque' END
          FROM equipments e JOIN item_metadata m ON m.equipment_id=e.id WHERE m.tipo_item='simples'""")
init_v04()

def catalog_row(db, code):
    return db.execute("""SELECT e.id,e.tipo,e.modelo,e.notas,m.tipo_item,m.id_serial,m.patrimonio,m.parent_id,m.funcao_no_conjunto,
    COALESCE(l.local,CASE WHEN m.parent_id IS NOT NULL THEN 'instalado' ELSE 'estoque' END) local
    FROM equipments e JOIN item_metadata m ON m.equipment_id=e.id
    LEFT JOIN component_locations l ON l.equipment_id=e.id WHERE e.id=?""",(code,)).fetchone()

@app.get('/api/catalog')
def catalog():
    with connect() as db:
        return [dict(r) for r in db.execute("""SELECT e.id,e.tipo,e.modelo,e.notas,m.tipo_item,m.id_serial,m.patrimonio,m.parent_id,m.funcao_no_conjunto,
        COALESCE(l.local,CASE WHEN m.parent_id IS NOT NULL THEN 'instalado' ELSE 'estoque' END) local
        FROM equipments e JOIN item_metadata m ON m.equipment_id=e.id LEFT JOIN component_locations l ON l.equipment_id=e.id
        ORDER BY CASE WHEN m.tipo_item='composto' THEN 0 ELSE 1 END,e.id""")]

@app.post('/api/catalog',status_code=201)
def catalog_create(v:ItemIn):
    code=(v.codigo or ('KIT-' if v.categoria=='kit' else 'CMP-')+__import__('uuid').uuid4().hex[:10]).strip().upper()
    if not code or len(code)>40: raise HTTPException(422,'Código inválido')
    if v.categoria=='kit' and v.tipo not in ('Monitoramento','TABSTER'): raise HTTPException(422,'Kit deve ser Monitoramento ou TABSTER')
    if v.categoria=='componente' and v.tipo not in ('Tablet','Scanner','Câmera','Outro'): raise HTTPException(422,'Tipo de componente inválido')
    serial=(v.id_serial or '').strip() or None
    patrimonio=(v.patrimonio or '').strip() or None
    parent=(v.parent_id or '').strip().upper() or None
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM equipments WHERE id=?',(code,)).fetchone(): raise HTTPException(409,'Código interno já cadastrado')
        if parent:
            kit=catalog_row(db,parent)
            if not kit or kit['tipo_item']!='composto' or v.categoria!='componente': raise HTTPException(422,'Kit pai inválido')
            if kit['tipo']=='Monitoramento' and v.tipo!='Câmera': raise HTTPException(422,'Monitoramento aceita câmeras')
            if kit['tipo']=='TABSTER' and v.tipo not in ('Tablet','Scanner'): raise HTTPException(422,'TABSTER aceita tablet/scanner')
            count=db.execute('SELECT COUNT(*) FROM item_metadata m JOIN equipments e ON e.id=m.equipment_id WHERE m.parent_id=? AND e.tipo=?',(parent,v.tipo)).fetchone()[0]
            limit=2 if kit['tipo']=='Monitoramento' else 1
            if count>=limit: raise HTTPException(409,'Limite de componentes deste tipo no kit')
            if kit['tipo']=='TABSTER' and v.tipo=='Tablet' and serial!=kit['id_serial']: raise HTTPException(422,'ID do tablet deve ser igual ao ID do kit')
        if serial:
            rows=db.execute('SELECT e.tipo,m.parent_id FROM item_metadata m JOIN equipments e ON e.id=m.equipment_id WHERE m.id_serial=?',(serial,)).fetchall()
            if rows and not (v.tipo=='Tablet' and parent and all(r['tipo']=='TABSTER' for r in rows) and len(rows)==1):
                raise HTTPException(409,'ID/serial já cadastrado')
        db.execute('INSERT INTO equipments(id,tipo,modelo,serial,notas,created_at) VALUES (?,?,?,?,?,?)',(code,v.tipo,v.modelo,serial or '[SEM SERIAL]',v.notas,utc()))
        db.execute('INSERT INTO item_metadata(equipment_id,tipo_item,possui_id,id_serial,possui_patrimonio,patrimonio,parent_id,funcao_no_conjunto) VALUES (?,?,?,?,?,?,?,?)',
          (code,'composto' if v.categoria=='kit' else 'simples',int(bool(serial)),serial,int(bool(patrimonio)),patrimonio,parent,v.funcao))
        if v.categoria=='componente':
            db.execute('INSERT INTO component_locations VALUES (?,?)',(code,'instalado' if parent else 'estoque'))
            db.execute('INSERT INTO component_history(at,component_id,kit_id,action,details) VALUES (?,?,?,?,?)',(utc(),code,parent,'cadastro',v.notas))
        log(db,'Cadastro '+v.categoria,code,v.tipo)
    return {'id':code}

@app.post('/api/catalog/{component_id}/link')
def catalog_link(component_id:str,v:LinkIn):
    parent=(v.parent_id or '').strip().upper() or None
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        c=catalog_row(db,component_id)
        if not c or c['tipo_item']!='simples': raise HTTPException(404,'Componente não encontrado')
        if c['parent_id'] and parent: raise HTTPException(409,'Desvincule primeiro o componente atual')
        if parent:
            kit=catalog_row(db,parent)
            if not kit or kit['tipo_item']!='composto': raise HTTPException(422,'Kit inválido')
            if (kit['tipo']=='Monitoramento' and c['tipo']!='Câmera') or (kit['tipo']=='TABSTER' and c['tipo'] not in ('Tablet','Scanner')): raise HTTPException(422,'Componente incompatível')
            if kit['tipo']=='TABSTER' and c['tipo']=='Tablet' and c['id_serial']!=kit['id_serial']: raise HTTPException(422,'ID do tablet deve coincidir com o ID do kit')
            count=db.execute('SELECT COUNT(*) FROM item_metadata m JOIN equipments e ON e.id=m.equipment_id WHERE m.parent_id=? AND e.tipo=?',(parent,c['tipo'])).fetchone()[0]
            if count >= (2 if kit['tipo']=='Monitoramento' else 1): raise HTTPException(409,'Kit já possui o máximo deste componente')
            if c['local']!='estoque': raise HTTPException(409,'Componente indisponível no estoque')
        db.execute('UPDATE item_metadata SET parent_id=?,funcao_no_conjunto=? WHERE equipment_id=?',(parent,v.funcao,component_id))
        db.execute('INSERT OR REPLACE INTO component_locations VALUES (?,?)',(component_id,'instalado' if parent else 'estoque'))
        db.execute('INSERT INTO component_history(at,component_id,kit_id,action,details) VALUES (?,?,?,?,?)',(utc(),component_id,parent,'vinculado' if parent else 'desvinculado','Alteração manual'))
        log(db,'Vínculo de componente',component_id,parent or 'estoque')
    return {'id':component_id,'parent_id':parent}

@app.post('/api/catalog/{component_id}/location')
def catalog_location(component_id:str,v:LocationIn):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        c=catalog_row(db,component_id)
        if not c or c['tipo_item']!='simples': raise HTTPException(404,'Componente não encontrado')
        if c['parent_id']: raise HTTPException(409,'Desvincule do kit antes de alterar disponibilidade')
        db.execute('INSERT OR REPLACE INTO component_locations VALUES (?,?)',(component_id,v.local))
        db.execute('INSERT INTO component_history(at,component_id,kit_id,action,details) VALUES (?,?,?,?,?)',(utc(),component_id,None,v.local,v.motivo))
        log(db,'Estado componente',component_id,v.local+': '+v.motivo)
    return {'id':component_id,'local':v.local}

@app.post('/api/swaps',status_code=201)
def swap(v:SwapIn):
    if v.defeituoso_id==v.substituto_id: raise HTTPException(422,'Componentes devem ser diferentes')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        kit=catalog_row(db,v.kit_id); old=catalog_row(db,v.defeituoso_id); new=catalog_row(db,v.substituto_id)
        if not kit or kit['tipo_item']!='composto': raise HTTPException(422,'Kit inválido')
        if not old or old['parent_id']!=v.kit_id: raise HTTPException(422,'Componente defeituoso não pertence ao kit')
        if not new or new['tipo_item']!='simples' or new['parent_id'] or new['local']!='estoque': raise HTTPException(409,'Substituto não está disponível no estoque')
        if old['tipo']!=new['tipo']: raise HTTPException(422,'Tipos incompatíveis')
        if kit['tipo']=='TABSTER' and new['tipo']=='Tablet' and new['id_serial']!=kit['id_serial']: raise HTTPException(422,'Tablet substituto deve ter o ID do TABSTER')
        ot=parse_ot(v.ot_id) if v.ot_id else None
        if ot: get_order(db,ot)
        db.execute('UPDATE item_metadata SET parent_id=NULL WHERE equipment_id=?',(v.defeituoso_id,))
        db.execute('UPDATE item_metadata SET parent_id=?,funcao_no_conjunto=? WHERE equipment_id=?',(v.kit_id,old['funcao_no_conjunto'],v.substituto_id))
        db.execute('INSERT OR REPLACE INTO component_locations VALUES (?,?)',(v.defeituoso_id,'manutencao'))
        db.execute('INSERT OR REPLACE INTO component_locations VALUES (?,?)',(v.substituto_id,'instalado'))
        cur=db.execute('INSERT INTO kit_swaps(at,kit_id,old_component,new_component,responsavel,motivo,ot_id) VALUES (?,?,?,?,?,?,?)',(utc(),v.kit_id,v.defeituoso_id,v.substituto_id,v.responsavel,v.motivo,ot))
        for code,action in [(v.defeituoso_id,'retirado_defeito'),(v.substituto_id,'instalado')]:
            db.execute('INSERT INTO component_history(at,component_id,kit_id,action,details) VALUES (?,?,?,?,?)',(utc(),code,v.kit_id,action,v.motivo+' / '+v.responsavel))
        log(db,'Substituição no kit',v.kit_id,v.defeituoso_id+' → '+v.substituto_id)
    return {'id':cur.lastrowid,'kit_id':v.kit_id,'retirado':v.defeituoso_id,'instalado':v.substituto_id}

@app.get('/api/swaps')
def swaps():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM kit_swaps ORDER BY id DESC')]

@app.get('/api/catalog/{component_id}/history')
def component_history(component_id:str):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM component_history WHERE component_id=? ORDER BY id DESC',(component_id,))]


# TestFlow v0.5: all new structures are additive. The existing database is never reseeded.
from datetime import timedelta
from zoneinfo import ZoneInfo
from fastapi.responses import StreamingResponse
from pydantic import model_validator
import math

UF_NAMES = {'AC':'Acre','AL':'Alagoas','AP':'Amapá','AM':'Amazonas','BA':'Bahia','CE':'Ceará','DF':'Distrito Federal','ES':'Espírito Santo','GO':'Goiás','MA':'Maranhão','MT':'Mato Grosso','MS':'Mato Grosso do Sul','MG':'Minas Gerais','PA':'Pará','PB':'Paraíba','PR':'Paraná','PE':'Pernambuco','PI':'Piauí','RJ':'Rio de Janeiro','RN':'Rio Grande do Norte','RS':'Rio Grande do Sul','RO':'Rondônia','RR':'Roraima','SC':'Santa Catarina','SP':'São Paulo','SE':'Sergipe','TO':'Tocantins'}

def init_v05():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS kit_destinations(kit_id TEXT PRIMARY KEY REFERENCES equipments(id), uf TEXT);
        CREATE TABLE IF NOT EXISTS commercial_orders(id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE, customer TEXT NOT NULL, created_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS commercial_lines(id INTEGER PRIMARY KEY AUTOINCREMENT, commercial_id INTEGER NOT NULL REFERENCES commercial_orders(id), kit_type TEXT NOT NULL, uf TEXT NOT NULL, requested INTEGER NOT NULL CHECK(requested>0));
        CREATE TABLE IF NOT EXISTS commercial_allocations(id INTEGER PRIMARY KEY AUTOINCREMENT, line_id INTEGER NOT NULL REFERENCES commercial_lines(id), kit_id TEXT NOT NULL REFERENCES equipments(id), linked_at TEXT NOT NULL, unlinked_at TEXT, shipped_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS only_one_active_commercial_allocation ON commercial_allocations(kit_id) WHERE unlinked_at IS NULL;
        CREATE TABLE IF NOT EXISTS reworks(id INTEGER PRIMARY KEY AUTOINCREMENT, kit_id TEXT NOT NULL REFERENCES equipments(id), order_id INTEGER NOT NULL REFERENCES orders(id), criterion TEXT NOT NULL, status TEXT NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT, retest_order_id INTEGER REFERENCES orders(id), technician TEXT, repair_notes TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS reworks_kit_status ON reworks(kit_id,status);
        """)
init_v05()

def kit_rows(db):
    return db.execute("SELECT e.id,e.tipo,e.modelo,m.patrimonio,d.uf FROM equipments e JOIN item_metadata m ON m.equipment_id=e.id LEFT JOIN kit_destinations d ON d.kit_id=e.id WHERE m.tipo_item='composto' AND e.tipo IN ('Monitoramento','TABSTER') ORDER BY e.id").fetchall()

def latest_kit_status(db,kit_id):
    ot=db.execute('SELECT id,result,status FROM orders WHERE equipment_id=? ORDER BY id DESC LIMIT 1',(kit_id,)).fetchone()
    if not ot: return 'Pendente'
    if ot['status']!='Concluída': return 'Em testes'
    if db.execute("SELECT 1 FROM reworks WHERE kit_id=? AND status!='Concluído' LIMIT 1",(kit_id,)).fetchone(): return 'Reprovado'
    return ot['result']

def is_released(db,kit_id): return latest_kit_status(db,kit_id)=='Aprovado'

class DestinationIn(BaseModel):
    uf: str | None = None

@app.get('/api/v05/ufs')
def ufs(): return [{'uf':k,'nome':v} for k,v in sorted(UF_NAMES.items())]

@app.put('/api/v05/kits/{kit_id}/uf')
def kit_destination(kit_id:str,v:DestinationIn):
    if v.uf and v.uf not in UF_NAMES: raise HTTPException(422,'UF inválida')
    with connect() as db:
        kit=db.execute("SELECT e.tipo FROM equipments e JOIN item_metadata m ON m.equipment_id=e.id WHERE e.id=? AND m.tipo_item='composto'",(kit_id,)).fetchone()
        if not kit: raise HTTPException(404,'Kit não encontrado')
        if db.execute('SELECT 1 FROM commercial_allocations WHERE kit_id=? AND unlinked_at IS NULL',(kit_id,)).fetchone(): raise HTTPException(409,'Kit vinculado a pedido; desvincule para alterar UF')
        db.execute('INSERT INTO kit_destinations(kit_id,uf) VALUES (?,?) ON CONFLICT(kit_id) DO UPDATE SET uf=excluded.uf',(kit_id,v.uf))
        log(db,'UF atualizada',kit_id,v.uf or 'Não definida')
    return {'id':kit_id,'uf':v.uf}

@app.get('/api/v05/kits')
def kits_v05(uf:str|None=None,kit_type:str|None=None):
    with connect() as db:
        rows=[]
        for r in kit_rows(db):
            x=dict(r)
            allocation=db.execute('SELECT co.number,cl.uf FROM commercial_allocations a JOIN commercial_lines cl ON cl.id=a.line_id JOIN commercial_orders co ON co.id=cl.commercial_id WHERE a.kit_id=? AND a.unlinked_at IS NULL',(r['id'],)).fetchone()
            x['uf']=allocation['uf'] if allocation else x['uf']
            x['commercial_order']=allocation['number'] if allocation else None
            x['status']=latest_kit_status(db,r['id'])
            if (not uf or x['uf']==uf) and (not kit_type or x['tipo']==kit_type): rows.append(x)
        return rows

class CommercialLineIn(BaseModel):
    kit_type: Literal['Monitoramento','TABSTER']
    uf: str
    requested: int = Field(ge=1,le=100000)
    @field_validator('uf')
    @classmethod
    def validate_uf(cls,v):
        if v not in UF_NAMES: raise ValueError('UF inválida')
        return v

class CommercialIn(BaseModel):
    number: str = Field(min_length=1,max_length=80)
    customer: str = Field(min_length=1,max_length=150)
    notes: str = Field(default='',max_length=1000)
    lines: list[CommercialLineIn] = Field(min_length=1)

@app.post('/api/v05/commercial',status_code=201)
def commercial_create(v:CommercialIn):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        try: cur=db.execute('INSERT INTO commercial_orders(number,customer,created_at,notes) VALUES (?,?,?,?)',(v.number.strip(),v.customer.strip(),utc(),v.notes))
        except sqlite3.IntegrityError: raise HTTPException(409,'Número comercial já cadastrado')
        order_id=cur.lastrowid
        for line in v.lines: db.execute('INSERT INTO commercial_lines(commercial_id,kit_type,uf,requested) VALUES (?,?,?,?)',(order_id,line.kit_type,line.uf,line.requested))
        log(db,'Pedido comercial criado',v.number,v.customer)
    return {'id':order_id,'number':v.number}

def commercial_report(db,co):
    lines=[]
    for r in db.execute('SELECT * FROM commercial_lines WHERE commercial_id=? ORDER BY id',(co['id'],)):
        alloc=[dict(a) for a in db.execute('SELECT * FROM commercial_allocations WHERE line_id=? AND unlinked_at IS NULL',(r['id'],))]
        released=sum(is_released(db,a['kit_id']) for a in alloc)
        shipped=sum(bool(a['shipped_at']) for a in alloc)
        other_allocated={x['kit_id'] for x in db.execute('SELECT kit_id FROM commercial_allocations WHERE unlinked_at IS NULL')}
        eligible=[dict(k) for k in kit_rows(db) if k['tipo']==r['kit_type'] and k['uf']==r['uf'] and k['id'] not in other_allocated]
        unallocated_approved=sum(is_released(db,k['id']) for k in eligible)
        available=len(eligible)
        total_alloc=len(alloc)
        still_to_link=max(0,r['requested']-total_alloc)
        lines.append({**dict(r),'allocated':total_alloc,'released':released,'shipped':shipped,'pending_allocation':still_to_link,'pending_release':max(0,r['requested']-released),'available_unallocated':available,'available_approved':unallocated_approved,'estimated_shortage':max(0,still_to_link-available),'allocations':[{**a,'status':latest_kit_status(db,a['kit_id'])} for a in alloc]})
    fully_released=all(l['released']>=l['requested'] for l in lines)
    fully_allocated=all(l['allocated']>=l['requested'] for l in lines)
    fully_shipped=all(l['shipped']>=l['requested'] for l in lines)
    return {**dict(co),'lines':lines,'status':'Expedido' if fully_shipped else ('Liberado' if fully_released else ('Alocado, aguardando testes' if fully_allocated else 'Aberto / parcial'))}

class AddCommercialLineIn(CommercialLineIn): pass

@app.post('/api/v05/commercial/{commercial_id}/lines',status_code=201)
def add_commercial_line(commercial_id:int,v:AddCommercialLineIn):
    with connect() as db:
        co=db.execute('SELECT 1 FROM commercial_orders WHERE id=?',(commercial_id,)).fetchone()
        if not co: raise HTTPException(404,'Pedido não encontrado')
        cur=db.execute('INSERT INTO commercial_lines(commercial_id,kit_type,uf,requested) VALUES (?,?,?,?)',(commercial_id,v.kit_type,v.uf,v.requested))
        log(db,'Linha comercial criada',str(commercial_id),v.kit_type+' '+v.uf)
        return {'id':cur.lastrowid}

@app.get('/api/v05/commercial')
def commercial_list():
    with connect() as db: return [commercial_report(db,r) for r in db.execute('SELECT * FROM commercial_orders ORDER BY id DESC')]

@app.get('/api/v05/commercial/{commercial_id}')
def commercial_detail(commercial_id:int):
    with connect() as db:
        r=db.execute('SELECT * FROM commercial_orders WHERE id=?',(commercial_id,)).fetchone()
        if not r: raise HTTPException(404,'Pedido não encontrado')
        return commercial_report(db,r)

class AllocationIn(BaseModel):
    kit_id: str

@app.post('/api/v05/commercial/lines/{line_id}/allocate',status_code=201)
def allocate(line_id:int,v:AllocationIn):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        line=db.execute('SELECT * FROM commercial_lines WHERE id=?',(line_id,)).fetchone()
        kit=db.execute("SELECT e.tipo,m.tipo_item FROM equipments e JOIN item_metadata m ON m.equipment_id=e.id WHERE e.id=?",(v.kit_id,)).fetchone()
        if not line or not kit or kit['tipo_item']!='composto': raise HTTPException(404,'Linha ou Kit não encontrado')
        if kit['tipo']!=line['kit_type']: raise HTTPException(422,'Tipo de Kit incompatível com pedido')
        if db.execute('SELECT COUNT(*) FROM commercial_allocations WHERE line_id=? AND unlinked_at IS NULL',(line_id,)).fetchone()[0]>=line['requested']: raise HTTPException(409,'Quantidade da linha já integralmente alocada')
        own=db.execute('SELECT uf FROM kit_destinations WHERE kit_id=?',(v.kit_id,)).fetchone()
        if own and own['uf'] and own['uf']!=line['uf']: raise HTTPException(409,'UF do Kit difere do pedido; corrija a UF antes de alocar')
        try: db.execute('INSERT INTO commercial_allocations(line_id,kit_id,linked_at) VALUES (?,?,?)',(line_id,v.kit_id,utc()))
        except sqlite3.IntegrityError: raise HTTPException(409,'Kit já vinculado a outro pedido ativo')
        db.execute('INSERT INTO kit_destinations(kit_id,uf) VALUES (?,?) ON CONFLICT(kit_id) DO UPDATE SET uf=excluded.uf',(v.kit_id,line['uf']))
        log(db,'Kit alocado',v.kit_id,'Linha '+str(line_id))
    return {'kit_id':v.kit_id,'line_id':line_id,'released':False,'status':'Alocado'}

@app.post('/api/v05/commercial/allocations/{allocation_id}/unlink')
def allocation_unlink(allocation_id:int):
    with connect() as db:
        a=db.execute('SELECT * FROM commercial_allocations WHERE id=? AND unlinked_at IS NULL',(allocation_id,)).fetchone()
        if not a: raise HTTPException(404,'Alocação não encontrada')
        if a['shipped_at']: raise HTTPException(409,'Kit expedido não pode ser desvinculado')
        db.execute('UPDATE commercial_allocations SET unlinked_at=? WHERE id=?',(utc(),allocation_id))
        log(db,'Kit desvinculado',a['kit_id'],str(allocation_id))
    return {'ok':True}

@app.post('/api/v05/commercial/allocations/{allocation_id}/ship')
def allocation_ship(allocation_id:int):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        a=db.execute('SELECT * FROM commercial_allocations WHERE id=? AND unlinked_at IS NULL',(allocation_id,)).fetchone()
        if not a: raise HTTPException(404,'Alocação não encontrada')
        if not is_released(db,a['kit_id']): raise HTTPException(409,'Kit não aprovado ou com retrabalho pendente')
        if a['shipped_at']: raise HTTPException(409,'Kit já expedido')
        db.execute('UPDATE commercial_allocations SET shipped_at=? WHERE id=?',(utc(),allocation_id))
        log(db,'Kit expedido',a['kit_id'],str(allocation_id))
    return {'ok':True}

@app.get('/api/v05/dashboard')
def dashboard_v05():
    with connect() as db:
        kits=kit_rows(db)
        states=[latest_kit_status(db,k['id']) for k in kits]
        total_tests=db.execute("SELECT COUNT(*) FROM orders o JOIN item_metadata m ON m.equipment_id=o.equipment_id WHERE m.tipo_item='composto' AND o.status='Concluída'").fetchone()[0]
        return {'kits':len(kits),'testes_realizados':total_tests,'aprovados':states.count('Aprovado'),'reprovados':states.count('Reprovado'),'pendentes':states.count('Pendente'),'em_teste':states.count('Em testes'),'retrabalhos_abertos':db.execute("SELECT COUNT(*) FROM reworks WHERE status!='Concluído'").fetchone()[0],'pedidos_abertos':sum(x['status']!='Expedido' for x in [commercial_report(db,r) for r in db.execute('SELECT * FROM commercial_orders')])}

@app.get('/api/v05/reworks')
def reworks_v05(status:str|None=None,technician:str|None=None):
    with connect() as db:
        query='SELECT r.*,o.tecnico AS tester FROM reworks r JOIN orders o ON o.id=r.order_id WHERE (? IS NULL OR r.status=?) AND (? IS NULL OR o.tecnico=?) ORDER BY r.id DESC'
        return [dict(r) for r in db.execute(query,(status,status,technician,technician))]

class RepairIn(BaseModel):
    status: Literal['Na manutenção','Aguardando reteste','Concluído']
    technician: str = Field(min_length=1,max_length=100)
    repair_notes: str = Field(default='',max_length=2000)

@app.put('/api/v05/reworks/{rework_id}')
def repair_update(rework_id:int,v:RepairIn):
    with connect() as db:
        r=db.execute('SELECT * FROM reworks WHERE id=?',(rework_id,)).fetchone()
        if not r: raise HTTPException(404,'Retrabalho não encontrado')
        if v.status=='Concluído' and not is_released(db,r['kit_id']): raise HTTPException(409,'Apenas o reteste aprovado pode encerrar o retrabalho')
        db.execute('UPDATE reworks SET status=?, technician=?,repair_notes=?,closed_at=? WHERE id=?',(v.status,v.technician,v.repair_notes,utc() if v.status=='Concluído' else None,rework_id))
        log(db,'Retrabalho atualizado',r['kit_id'],v.status)
    return {'id':rework_id,'status':v.status}

@app.get('/api/v05/reports/weekly.pdf')
def weekly_pdf(start:str|None=None,commercial_id:int|None=None,technician:str|None=None):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from fastapi.responses import Response
    font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    if os.path.isfile(font) and 'DejaVuTF' not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(TTFont('DejaVuTF',font))
    family='DejaVuTF' if 'DejaVuTF' in pdfmetrics.getRegisteredFontNames() else 'Helvetica'
    today=datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    try: week=datetime.strptime(start,'%Y-%m-%d').date() if start else today-timedelta(days=today.weekday())
    except ValueError: raise HTTPException(422,'Data inválida. Use AAAA-MM-DD')
    week-=timedelta(days=week.weekday())
    end=week+timedelta(days=7)
    def in_week(date_string):
        if not date_string: return False
        try: return week<=datetime.fromisoformat(date_string).astimezone(ZoneInfo('America/Sao_Paulo')).date()<end
        except (ValueError,TypeError): return False
    with connect() as db:
        rows=[dict(r) for r in db.execute("SELECT o.*,e.tipo FROM orders o JOIN equipments e ON e.id=o.equipment_id JOIN item_metadata m ON m.equipment_id=e.id WHERE m.tipo_item='composto' ORDER BY o.id")]
        if commercial_id is not None:
            ids={r['kit_id'] for r in db.execute('SELECT a.kit_id FROM commercial_allocations a JOIN commercial_lines l ON l.id=a.line_id WHERE l.commercial_id=?',(commercial_id,))}
            rows=[r for r in rows if r['equipment_id'] in ids]
        if technician: rows=[r for r in rows if r['tecnico'].casefold()==technician.casefold()]
        completed=[r for r in rows if r['status']=='Concluída' and in_week(r['ended'])]
        unique=len({r['equipment_id'] for r in completed})
        approved=sum(r['result']=='Aprovado' for r in completed)
        failed=sum(r['result']=='Reprovado' for r in completed)
        reworks=[r for r in db.execute('SELECT * FROM reworks') if in_week(r['opened_at']) and (commercial_id is None or any(o['equipment_id']==r['kit_id'] for o in rows))]
        current={r['equipment_id']:latest_kit_status(db,r['equipment_id']) for r in completed}
        swaps=[r for r in db.execute('SELECT * FROM kit_swaps') if in_week(r['at']) and (commercial_id is None or any(o['equipment_id']==r['kit_id'] for o in rows))]
        lines=[f'Período: {week.strftime("%d/%m/%Y")} a {(end-timedelta(days=1)).strftime("%d/%m/%Y")}',f'Filtro de pedido: {commercial_id if commercial_id else "Todos"} | Tester: {technician or "Todos"}',f'Kits distintos com teste concluído na semana: {unique}',f'Execuções concluídas (incluindo retestes): {len(completed)}',f'Resultados das execuções: {approved} aprovadas / {failed} reprovadas',f'Situação ATUAL desses Kits: {sum(v=="Aprovado" for v in current.values())} aprovados / {sum(v=="Reprovado" for v in current.values())} reprovados / {sum(v=="Em testes" for v in current.values())} em testes',f'Retrabalhos abertos na semana: {len(reworks)} | Substituições: {len(swaps)}','', 'TESTES CONCLUÍDOS NA SEMANA']
        lines += [f"OT-{r['id']:04d} | {r['equipment_id']} | {r['tipo']} | {r['result']} | Tester: {r['tecnico']}" for r in completed]
        lines += ['','RETRABALHOS ABERTOS NA SEMANA']+[f"{r['kit_id']} | {r['criterion']} | {r['status']}" for r in reworks]
        if not completed: lines+=['Nenhum teste concluído para os filtros.']
        bio=io.BytesIO(); c=canvas.Canvas(bio,pagesize=A4); width,height=A4
        y=height-52
        c.setFont(family,16);c.drawString(38,y,'TestFlow - Relatório semanal');y-=33
        c.setFont(family,9)
        for ln in lines:
            for part in simpleSplit(ln,family,9,width-76) or ['']:
                if y<48: c.showPage();c.setFont(family,9);y=height-52
                c.drawString(38,y,part);y-=15
        c.save()
        return Response(bio.getvalue(),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="testflow-semana-{week.isoformat()}.pdf"'})
