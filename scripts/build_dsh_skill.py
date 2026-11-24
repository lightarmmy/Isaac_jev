"""Build one portable skill folder/ZIP, including the stdlib game runtime, never weights."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
skill=ROOT/"skills/isaac-play"
vendor=skill/"scripts/vendor/isaac_jev"
shutil.copytree(ROOT/"src/isaac_jev",vendor,dirs_exist_ok=True,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
shutil.copytree(ROOT/"mods/isaac_jev_bridge",skill/"assets/isaac_jev_bridge",dirs_exist_ok=True)
(skill/"connection.json").write_text(json.dumps({"project_root":str(ROOT),"port":9530,"bridge_port":9527,"policy_port":19528},indent=2),encoding="utf-8")
files=[p for p in skill.rglob("*") if p.is_file() and p.name!="manifest.json" and "__pycache__" not in p.parts and p.suffix!=".pyc"]
manifest={p.relative_to(skill).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(skill/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
output=ROOT/".local/isaac-play-skill.zip";output.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
    for p in files+[skill/"manifest.json"]:archive.write(p,"isaac-play/"+p.relative_to(skill).as_posix())
print(json.dumps({"skill":str(skill),"zip":str(output),"bytes":output.stat().st_size,"files":len(files)+1,"weights_included":False}))
