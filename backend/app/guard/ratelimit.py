from __future__ import annotations
from collections import defaultdict,deque
from time import monotonic
_calls=defaultdict(deque)
def check(agent_id:str,calls:int,per_seconds:int)->bool:
    now=monotonic(); q=_calls[agent_id]
    while q and q[0]<now-per_seconds:q.popleft()
    q.append(now);return len(q)<=calls
def reset():_calls.clear()
