"""Cria 20 kits Monitoramento + 20 kits TABSTER e 80 componentes ficticios.
Uso: python gerar_40_kits.py --executar (sem --executar apenas prévia).
Idempotente: verifica cada código e não duplica. Banco SQLite local.
"""
import argparse
from app import connect, utc, log

MARK='[DEMO-KITS-V04]'

def plan():
    rows=[]
    for n in range(1,21):
        k=f'DEMO-MON-{n:03d}'
        rows.append((k,'Monitoramento','Kit Monitoramento demonstrativo',f'MON-DEMO-{n:03d}',f'PAT-MON-{n:03d}',None,None,'composto'))
        for i in (1,2):
            rows.append((f'DEMO-CAM-{n:03d}-{i}','Câmera','Câmera demonstrativa',f'CAM-DEMO-{n:03d}-{i}',f'PAT-CAM-{n:03d}-{i}',k,f'Câmera {i}','simples'))
        t=f'DEMO-TABSTER-{n:03d}'
        sid=f'TABSTER-DEMO-{n:03d}'
        rows.append((t,'TABSTER','Kit TABSTER demonstrativo',sid,f'PAT-KIT-TAB-{n:03d}',None,None,'composto'))
        rows.append((f'DEMO-TABLET-{n:03d}','Tablet','Tablet demonstrativo',sid,f'PAT-TABLET-{n:03d}',t,'Tablet','simples'))
        rows.append((f'DEMO-SCANNER-{n:03d}','Scanner','Scanner demonstrativo',f'SCN-DEMO-{n:03d}',f'PAT-SCANNER-{n:03d}',t,'Scanner','simples'))
    return rows

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--executar',action='store_true')
    args=parser.parse_args()
    rows=plan()
    with connect() as db:
        existing={r['id']:r for r in db.execute('SELECT * FROM equipments')}
        meta={r['equipment_id']:r for r in db.execute('SELECT * FROM item_metadata')}
        new=[]
        for code,tipo,modelo,serial,pat,parent,role,category in rows:
            if code in existing:
                m=meta.get(code)
                if not m or existing[code]['tipo']!=tipo or m['id_serial']!=serial or m['patrimonio']!=pat or m['parent_id']!=parent or m['tipo_item']!=category:
                    raise SystemExit(f'CONFLITO: {code}. Nada foi gravado.')
            else:new.append(code)
        print('Plano: 40 kits e 80 componentes; novos:',len(new),'existentes correspondentes:',len(rows)-len(new))
        if not args.executar:
            print('PREVIA: nenhuma alteração realizada. Use --executar para cadastrar.')
            return
        if input('Digite CADASTRAR-120 para confirmar: ')!='CADASTRAR-120':
            print('Cancelado.');return
        db.execute('BEGIN IMMEDIATE')
        for code,tipo,modelo,serial,pat,parent,role,category in rows:
            if code not in new:continue
            db.execute('INSERT INTO equipments(id,tipo,modelo,serial,notas,created_at) VALUES (?,?,?,?,?,?)',(code,tipo,modelo,serial,MARK+' Dados inteiramente fictícios',utc()))
        for code,tipo,modelo,serial,pat,parent,role,category in rows:
            if code not in new:continue
            db.execute('INSERT INTO item_metadata(equipment_id,tipo_item,possui_id,id_serial,possui_patrimonio,patrimonio,parent_id,funcao_no_conjunto) VALUES (?,?,?,?,?,?,?,?)',(code,category,1,serial,1,pat,parent,role))
            if category=='simples':
                db.execute('INSERT INTO component_locations(equipment_id,local) VALUES (?,?)',(code,'instalado'))
                db.execute('INSERT INTO component_history(at,component_id,kit_id,action,details) VALUES (?,?,?,?,?)',(utc(),code,parent,'cadastro',MARK))
            log(db,'Cadastro DEMO',code,MARK)
        print('SUCESSO: inseridos',len(new),'registros; existentes preservados.')

if __name__=='__main__':main()
