"""Persistent, source-backed item summaries; missing facts stay missing."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

KINDS={"collectible","trinket","card","pill","pickup"}


class KnowledgeStore:
    def __init__(self,root):
        self.root=Path(root);self.items=self.root/"items";self.history=self.root/"history"
        self.items.mkdir(parents=True,exist_ok=True);self.history.mkdir(parents=True,exist_ok=True)

    def key(self,kind,item_id,version="Repentance+"):
        if kind not in KINDS or type(item_id) is not int or not 0<item_id<100000:raise ValueError("Invalid item kind/ID")
        if version not in {"Repentance+","Repentance","Afterbirth+","Rebirth"}:raise ValueError("Unsupported game version")
        return version.replace("+","plus")+"-"+kind+"-"+str(item_id)

    def get(self,kind,item_id,version="Repentance+"):
        key=self.key(kind,item_id,version);path=self.items/(key+".json")
        if not path.exists():return {"status":"missing","item_kind":kind,"item_id":item_id,"game_version":version,"cache_hit":False}
        return {**json.loads(path.read_text(encoding="utf-8")),"cache_hit":True}

    def lookup(self,requests):
        if not isinstance(requests,list) or len(requests)>64:raise ValueError("Use a list of at most 64 items")
        return [self.get(r.get("item_kind","collectible"),r["item_id"],r.get("game_version","Repentance+")) for r in requests]

    def upsert(self,entry):
        if not isinstance(entry,dict):raise ValueError("Entry must be an object")
        key=self.key(entry.get("item_kind"),entry.get("item_id"),entry.get("game_version","Repentance+"))
        sources=entry.get("sources",[])
        if not sources or len(sources)>10:raise ValueError("Provide Wiki sources and excerpts")
        for source in sources:
            parsed=urlsplit(source.get("url",""))
            if parsed.scheme!="https" or parsed.hostname!="isaac.huijiwiki.com" or parsed.username or parsed.password or parsed.port not in {None,443}:
                raise ValueError("Use an HTTPS isaac.huijiwiki.com page URL")
            excerpt=source.get("excerpt","")
            if not isinstance(excerpt,str) or not 20<=len(excerpt)<=12000:raise ValueError("Provide a substantive page excerpt")
            if any(s in excerpt.lower() for s in ("_cf_chl_opt","enable javascript and cookies","启用 javascript 和 cookie","just a moment")):
                raise ValueError("A browser verification page is not item evidence")
        if not isinstance(entry.get("summary"),str) or not 1<=len(entry["summary"])<=8000:raise ValueError("Provide a concise item summary")
        if entry.get("status","partial") not in {"ready","partial"}:raise ValueError("Imported summaries are ready or partial")
        if entry.get("status")=="ready" and (entry.get("identity_checked") is not True or not entry.get("applies_to")):
            raise ValueError("Ready entries need checked page identity and explicit applicable version")
        # Source claims and project compatibility assessments remain separate.
        allowed={"item_kind","item_id","game_version","names","aliases","summary","effects","stat_changes","synergies",
                 "item_type","quality","sources","status","identity_checked","applies_to","combat_support","unknown_fields"}
        clean={k:v for k,v in entry.items() if k in allowed}
        clean["game_version"]=entry.get("game_version","Repentance+");clean["status"]=entry.get("status","partial")
        text=json.dumps(clean,ensure_ascii=False,sort_keys=True,allow_nan=False)
        if len(text)>100000:raise ValueError("Entry too large")
        revision=hashlib.sha256(text.encode()).hexdigest()[:16]
        clean.update(knowledge_revision=revision,updated_at=datetime.now(timezone.utc).isoformat())
        path=self.items/(key+".json")
        if path.exists():
            old=json.loads(path.read_text(encoding="utf-8"))
            if old.get("knowledge_revision")==revision:return {**old,"unchanged":True}
            (self.history/(key+"-"+old.get("knowledge_revision","initial")+".json")).write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding="utf-8")
        temp=path.with_suffix(".tmp");temp.write_text(json.dumps(clean,ensure_ascii=False,indent=2),encoding="utf-8");temp.replace(path)
        return clean

    def search(self,kind,item_id,names):
        self.key(kind,item_id)
        if not isinstance(names,list) or not names or len(names)>8:raise ValueError("Provide visible item names")
        names=[str(n)[:200] for n in names if n]
        terms=" ".join(names[:2])
        return {"item_kind":kind,"item_id":item_id,"query":"site:isaac.huijiwiki.com "+terms,
            "search_url":"https://isaac.huijiwiki.com/wiki/特殊:搜索?search="+quote(terms,safe=""),
            "status":"use_harness_web_tool","instructions":"Read the matching Wiki page with the harness browser/search tool; check kind, ID and version; import excerpts with knowledge_upsert."}

    def read_request(self,url):
        parsed=urlsplit(url)
        if parsed.scheme!="https" or parsed.hostname!="isaac.huijiwiki.com" or parsed.username or parsed.password:
            raise ValueError("Use the supplied Wiki domain")
        return {"url":url,"status":"browser_required","instructions":"Use dsh's webpage/browser tool to read the article. Return a source excerpt, page title and version to knowledge_upsert. HTTP-only retrieval encountered verification pages; do not cache those."}
