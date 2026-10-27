"""Gera OTs fictícias para equipamentos JÁ cadastrados/importados.
Não altera cadastro nem estoque. Seguro para reexecutar.
"""
import argparse
from datetime import datetime, timedelta, timezone
from app import connect, CRITERIA, log

MARK = '[DEMO-OT-V03]'

def main():
    parser = argparse.ArgumentParser(description='Gera ordens fictícias para equipamentos cadastrados')
    parser.add_argument('--quantidade', type=int, default=15, help='Quantidade máxima de OTs, padrão 15')
    args = parser.parse_args()
    if not 1 <= args.quantidade <= 200:
        parser.error('Quantidade deve ser entre 1 e 200')
    with connect() as db:
        equipment = db.execute('SELECT id,tipo FROM equipments ORDER BY id').fetchall()
        if not equipment:
            print('Nenhum equipamento cadastrado. Importe primeiro seu CSV pelo fluxo de cadastro existente.')
            return
        already = db.execute('SELECT COUNT(*) FROM orders WHERE notes LIKE ?', ('%'+MARK+'%',)).fetchone()[0]
        if already:
            print(f'Já existem {already} OTs demonstrativas desta versão. Nenhuma OT duplicada.')
            return
        now = datetime.now(timezone.utc)
        count = min(args.quantidade, len(equipment))
        results = ['Aprovado','Aprovado','Reprovado','Aprovado','Reprovado','Em aberto']
        created = []
        db.execute('BEGIN IMMEDIATE')
        for i, eq in enumerate(equipment[:count]):
            result = results[i % len(results)]
            start = now - timedelta(days=count-i, minutes=30+i*4)
            ended = (start+timedelta(minutes=35+(i%5)*12)).isoformat() if result!='Em aberto' else None
            cur = db.execute('''INSERT INTO orders
                (equipment_id,origem,responsavel,tecnico,status,result,started,ended,notes)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (eq['id'], 'Produção' if i%2==0 else 'Manutenção',
                 'Equipe DEMO', f'Técnico DEMO {(i%3)+1}',
                 'Em aberto' if result=='Em aberto' else 'Concluída',result,
                 start.isoformat(),ended,
                 MARK+' DADOS FICTÍCIOS — NÃO USAR PARA LIBERAÇÃO REAL'))
            ot=cur.lastrowid
            if result!='Em aberto':
                criteria=CRITERIA.get(eq['tipo'],CRITERIA['Outro'])
                db.executemany('INSERT INTO checks(order_id,criterion,result) VALUES (?,?,?)',
                    [(ot,c,'Reprovado' if result=='Reprovado' and j==len(criteria)-1 else 'Aprovado') for j,c in enumerate(criteria)])
            log(db,'OT demonstrativa',f'OT-{ot:04d}',f'{MARK} {eq["id"]} / {result}')
            created.append((ot,eq['id'],result))
        # Criar um reteste concluído para a primeira OT reprovada, preservando a ligação com a original.
        failed = next((x for x in created if x[2]=='Reprovado'),None)
        if failed:
            original,eqid,_=failed
            eqtype=next(e['tipo'] for e in equipment if e['id']==eqid)
            start=now-timedelta(hours=2)
            cur=db.execute('''INSERT INTO orders
                (equipment_id,origem,responsavel,tecnico,status,result,started,ended,notes,parent_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (eqid,'Retrabalho','Equipe DEMO','Técnico DEMO 1','Concluída','Aprovado',
                 start.isoformat(),(start+timedelta(minutes=45)).isoformat(),
                 MARK+' RETESTE FICTÍCIO — falha corrigida',original))
            db.executemany('INSERT INTO checks(order_id,criterion,result) VALUES (?,?,?)',
                           [(cur.lastrowid,c,'Aprovado') for c in CRITERIA.get(eqtype,CRITERIA['Outro'])])
            log(db,'Reteste demonstrativo',f'OT-{cur.lastrowid:04d}',f'{MARK} OT-{original:04d}')
            print(f'Reteste OT-{cur.lastrowid:04d} vinculado à OT-{original:04d}')
        print(f'Criadas {count} OTs vinculadas a equipamentos existentes.' + (' + 1 reteste.' if failed else ''))
        print('Reinicie/atualize a página do dashboard para visualizar relatórios e exportar CSV.')

if __name__=='__main__':
    main()
