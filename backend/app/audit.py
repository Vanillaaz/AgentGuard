from __future__ import annotations
import hashlib, json, uuid
from datetime import datetime, timezone
from .db import connection

FIELDS = ('id','timestamp','agent_id','session_id','tool','arguments_json','decision','score','reasons_json','shadow','prev_hash')
def canonical(row: dict) -> str: return json.dumps({k:row[k] for k in FIELDS}, sort_keys=True, separators=(',', ':'))
def write_event(agent_id:str, session_id:str, tool:str, arguments:dict, decision:str, score:int, reasons:list[str], shadow:bool=False) -> str:
    c=connection(); last=c.execute('SELECT hash FROM events ORDER BY rowid DESC LIMIT 1').fetchone()
    row={'id':str(uuid.uuid4()),'timestamp':datetime.now(timezone.utc).isoformat(),'agent_id':agent_id,'session_id':session_id,'tool':tool,'arguments_json':json.dumps(arguments,sort_keys=True),'decision':decision,'score':score,'reasons_json':json.dumps(reasons),'shadow':int(shadow),'prev_hash':last['hash'] if last else 'GENESIS'}
    digest=hashlib.sha256((row['prev_hash']+canonical(row)).encode()).hexdigest(); c.execute(f"INSERT INTO events ({','.join(FIELDS)},hash) VALUES ({','.join('?' for _ in FIELDS)},?)",[*[row[k] for k in FIELDS],digest]); c.commit();c.close();return row['id']
def verify() -> dict:
    c=connection(); previous='GENESIS'; count=0
    for r in c.execute('SELECT * FROM events ORDER BY rowid'):
        row=dict(r); expected=hashlib.sha256((previous+canonical(row)).encode()).hexdigest()
        if row['prev_hash']!=previous or row['hash']!=expected: c.close(); return {'valid':False,'broken_event_id':row['id'],'verified_count':count}
        previous=row['hash']
        count += 1
    c.close(); return {'valid':True,'broken_event_id':None,'verified_count':count}
