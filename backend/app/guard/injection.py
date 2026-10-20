from .llm import injection_check, Injection
QUARANTINE='[AgentGuard: content quarantined, possible prompt injection]'
def scan_output(value):
    result=injection_check(str(value)); return (QUARANTINE,result) if result.injection_detected else (value,result)
