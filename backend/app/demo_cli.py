from .db import init_db
from .demo.scenarios import run
if __name__=='__main__':
    init_db()
    for name in ('normal_task','data_leak','prompt_injection','suspicious_payment','runaway_loop'):
        result=run(name); print(f"{name}: "+', '.join(x['decision'] for x in result['actions']))
