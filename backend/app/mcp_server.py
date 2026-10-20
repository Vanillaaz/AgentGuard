"""MCP front door. Run with `python -m backend.app.mcp_server` when mcp is installed."""
from __future__ import annotations
import os
from .guard.engine import Request, evaluate
from .tools.mock_tools import execute
from .guard.injection import scan_output
try:
    from mcp.server.fastmcp import FastMCP
    mcp=FastMCP('AgentGuard')
    def guarded(tool, **arguments):
        d=evaluate(Request(agent_id=os.getenv('AGENT_ID','finance-agent'),session_id=os.getenv('AGENT_SESSION_ID','mcp'),task=os.getenv('AGENT_TASK','Use business tools safely'),tool=tool,arguments=arguments))
        output=execute(tool,arguments) if d.decision=='ALLOW' else None
        if output is not None: output,_=scan_output(output)
        return {'decision':d.decision,'reasons':d.reasons,'result':output}
    @mcp.tool()
    def read_document(doc_id:str): return guarded('read_document',doc_id=doc_id)
    @mcp.tool()
    def query_database(table:str,filter:str=''): return guarded('query_database',table=table,filter=filter)
    @mcp.tool()
    def make_payment(vendor:str,amount:float,currency:str='INR'): return guarded('make_payment',vendor=vendor,amount=amount,currency=currency)
    @mcp.tool()
    def send_email(to:str,subject:str,body:str): return guarded('send_email',to=to,subject=subject,body=body)
    @mcp.tool()
    def delete_record(table:str,record_id:str): return guarded('delete_record',table=table,record_id=record_id)
    if __name__=='__main__':mcp.run()
except ImportError:
    mcp=None
