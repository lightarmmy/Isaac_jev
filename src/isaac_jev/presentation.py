"""Short, explicit agent activity for the game HUD, never private reasoning."""
import time
import uuid


class AgentPresentation:
    def __init__(self):self.calls={};self.report=None;self.last_tool=None

    def begin(self,tool):
        token=uuid.uuid4().hex;self.calls[token]=(tool,time.monotonic());return token

    def end(self,token):
        call=self.calls.pop(token,None)
        if call:self.last_tool=(call[0],time.monotonic())

    def set(self,state,summary,ttl_s=60):
        if state not in {"thinking","idle"}:raise ValueError("State must be thinking or idle")
        if not isinstance(summary,str) or len(summary)>120:raise ValueError("Use a summary up to 120 characters")
        self.report={"agent_state":state,"summary":" ".join(summary.split()),"expires":time.monotonic()+ttl_s}

    def snapshot(self):
        if self.calls:
            tool,_=max(self.calls.values(),key=lambda row:row[1])
            return {"agent_state":"tool","tool":tool,"summary":""}
        fresh=self.report and time.monotonic()<self.report["expires"]
        if fresh and self.report["agent_state"]=="thinking":
            return {k:v for k,v in self.report.items() if k!="expires"}
        if self.last_tool and time.monotonic()-self.last_tool[1]<3:
            return {"agent_state":"recent_tool","tool":self.last_tool[0],"summary":""}
        if fresh:return {k:v for k,v in self.report.items() if k!="expires"}
        return {"agent_state":"idle","tool":"","summary":"Waiting for dsh"}
