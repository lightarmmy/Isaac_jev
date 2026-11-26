"""Project-scoped SSH/SFTP administration; password supplied by prompt/environment."""
import argparse
import getpass
import json
import os
from pathlib import Path
import shlex
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.local/serverdeps'))
import paramiko

def main():
    p=argparse.ArgumentParser();p.add_argument('--exec-file',type=Path);p.add_argument('--upload-manifest',type=Path)
    p.add_argument('--host',default='10.28.0.22');p.add_argument('--port',type=int,default=31203);p.add_argument('--user',default='root')
    a=p.parse_args();client=paramiko.SSHClient();client.load_host_keys(str(ROOT/'.local/isaac-lab/ssh-known-hosts'))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    password=os.environ.get('ISAAC_SERVER_PASSWORD') or getpass.getpass('SSH password: ')
    try:
        client.connect(a.host,port=a.port,username=a.user,password=password,allow_agent=False,look_for_keys=False,timeout=10);password=None
        if a.upload_manifest:
            sftp=client.open_sftp()
            for row in json.loads(a.upload_manifest.read_text(encoding='utf-8')):
                local=Path(row['local']).resolve();remote=row['remote']
                if not local.is_relative_to(ROOT) or not remote.startswith('/xufanghui/isaac-jev-v005/') or '..' in remote.split('/'):
                    raise ValueError('Upload outside task project')
                client.exec_command('mkdir -p -- '+shlex.quote(str(Path(remote).parent).replace('\\','/')))[1].channel.recv_exit_status()
                try:
                    if row.get('immutable') and sftp.stat(remote).st_size==local.stat().st_size:print(json.dumps({'exists':remote}),flush=True);continue
                except OSError:pass
                sftp.put(str(local),remote+'.uploading');sftp.posix_rename(remote+'.uploading',remote)
                print(json.dumps({'uploaded':remote,'bytes':local.stat().st_size}),flush=True)
            sftp.close()
        if a.exec_file:
            data=a.exec_file.read_bytes();sftp=client.open_sftp();remote='/xufanghui/isaac-jev-v005/.admin-task.sh'
            client.exec_command('mkdir -p /xufanghui/isaac-jev-v005')[1].channel.recv_exit_status()
            with sftp.file(remote,'wb') as out:out.write(data)
            sftp.close();_,out,err=client.exec_command('bash '+shlex.quote(remote),timeout=60)
            for line in out:print(line,end='',flush=True)
            error=err.read().decode();code=out.channel.recv_exit_status()
            if error:print(error,file=sys.stderr,end='')
            if code:raise SystemExit(code)
    finally:password=None;client.close()

if __name__=='__main__':main()
