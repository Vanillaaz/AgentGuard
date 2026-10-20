from __future__ import annotations
from typing import Any
import yaml
from pydantic import BaseModel, Field
from ..config import POLICY_PATH
class Thresholds(BaseModel): escalate:int=30; block:int=70
class Limits(BaseModel): max_payment:float|None=None; hard_max_payment:float|None=None
class Rate(BaseModel): calls:int=20; per_seconds:int=10
class Agent(BaseModel): allowed_tools:list[str]=Field(default_factory=list); limits:Limits=Field(default_factory=Limits); rate_limit:Rate=Field(default_factory=Rate)
class Global(BaseModel):
    blocked_email_domains:list[str]=[]; internal_email_domains:list[str]=[]; sensitive_patterns:list[str]=[]; tools_requiring_approval:list[str]=[]; bulk_record_limit:int=100
class Policy(BaseModel): version:int=1; thresholds:Thresholds=Field(default_factory=Thresholds); global_:Global=Field(alias='global'); agents:dict[str,Agent]={}
def load_policy(path=POLICY_PATH)->Policy:
    with open(path,encoding='utf8') as f:return Policy.model_validate(yaml.safe_load(f))
