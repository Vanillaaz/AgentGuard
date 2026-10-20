from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .policy import Policy, Agent
@dataclass
class Finding: rule_id:str; verdict:str; severity:int; reason:str
def _sensitive(a:dict, patterns:list[str])->bool:
    text=str(a).lower()
    return any(p in text for p in patterns) or any(x in text for x in ('@','salary','phone','aadhaar','pan'))
def tool_not_allowed(tool:str, args:dict, agent:Agent, *_)->list[Finding]:
    return [Finding('tool_not_allowed','block',100,f'{tool} is not permitted for this agent')] if tool not in agent.allowed_tools else []
def payment_over_limit(tool:str,args:dict,agent:Agent,*_)->list[Finding]:
    if tool!='make_payment': return []
    amount=float(args.get('amount',0)); lim=agent.limits
    if lim.hard_max_payment is not None and amount>lim.hard_max_payment:return [Finding('payment_over_limit','block',100,f'Payment {amount:g} exceeds hard limit {lim.hard_max_payment:g}')]
    if lim.max_payment is not None and amount>lim.max_payment:return [Finding('payment_over_limit','escalate',65,f'Payment {amount:g} exceeds approval limit {lim.max_payment:g}')]
    return []
def external_data_exfiltration(tool:str,args:dict,agent:Agent,policy:Policy,*_)->list[Finding]:
    if tool!='send_email':return []
    domain=str(args.get('to','')).rsplit('@',1)[-1].lower(); g=policy.global_
    if domain in g.blocked_email_domains:return [Finding('blocked_email_domain','block',100,f'Email domain {domain} is blocked')]
    if domain not in g.internal_email_domains and _sensitive(args,g.sensitive_patterns):return [Finding('external_data_exfiltration','block',95,'Sensitive data cannot be emailed outside the company')]
    return []
def approval_required_tool(tool:str,args:dict,agent:Agent,policy:Policy,*_)->list[Finding]:
    return [Finding('approval_required_tool','escalate',50,f'{tool} requires human approval')] if tool in policy.global_.tools_requiring_approval else []
def bulk_action(tool:str,args:dict,agent:Agent,policy:Policy,*_)->list[Finding]:
    n=int(args.get('record_count',args.get('limit',0)) or 0)
    return [Finding('bulk_action','escalate',45,f'Bulk action affects {n} records')] if tool in ('query_database','delete_record') and n>policy.global_.bulk_record_limit else []
def unusual_amount(tool:str,args:dict,agent:Agent,policy:Policy,recent_average:float=0)->list[Finding]:
    amount=float(args.get('amount',0) or 0)
    return [Finding('unusual_amount','escalate',55,f'Payment is over 5x recent average ({recent_average:g})')] if tool=='make_payment' and recent_average>0 and amount>recent_average*5 else []
RULES=[tool_not_allowed,payment_over_limit,external_data_exfiltration,approval_required_tool,bulk_action,unusual_amount]
