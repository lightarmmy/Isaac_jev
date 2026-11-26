"""User-requested, loopback SSH forwarding. Credentials stay in memory only."""
import asyncio
import ipaddress
from pathlib import Path
import select
import socket
import socketserver
import sys
import threading
import time


def connection_options(payload):
    host=payload.get("host")
    try:ipaddress.ip_address(host)
    except (ValueError,TypeError):raise ValueError("Use an IP address, not a command or URL") from None
    port=payload.get("port",31203);username=payload.get("username","root")
    password=payload.get("password")
    if type(port) is not int or not 1<=port<=65535:raise ValueError("Invalid SSH port")
    if not isinstance(username,str) or not username or len(username)>64 or any(c.isspace() or ord(c)<32 for c in username):raise ValueError("Invalid username")
    if password is not None and (not isinstance(password,str) or not 1<=len(password)<=512):raise ValueError("Invalid password")
    return host,port,username,password


def prompt_password():
    import tkinter as tk
    from tkinter import simpledialog
    window=tk.Tk();window.withdraw();window.attributes("-topmost",True)
    try:return simpledialog.askstring("Isaac Jev SSH","Server password (not saved):",show="*",parent=window)
    finally:window.destroy()


class ForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True
    block_on_close=False

    def get_request(self):
        connection,address=super().get_request()
        connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        return connection,address


class ServerLink:
    def __init__(self,root,local_port=19528):
        self.root=Path(root);self.local_port=local_port
        self.state={"state":"disconnected","ready":False,"error":None}
        self.client=None;self.forwarder=None;self.job=None;self.generation=0
        self.last_probe=0

    def status(self):return dict(self.state)

    async def probe(self):
        from .remote import PolicyClient, validate_description
        client=PolicyClient(self.local_port,2)
        try:
            await client.connect();reply=await client.request({"command":"describe"});validate_description(reply)
            return True
        except (OSError,RuntimeError,ValueError,asyncio.TimeoutError):return False
        finally:await client.close()

    def begin(self,payload):
        options=connection_options(payload)
        if self.job and not self.job.done():return {"ok":False,"error":"Connection request already running"}
        if self.client:return {"ok":False,"error":"Disconnect the managed tunnel before connecting another server"}
        self.generation+=1;generation=self.generation
        host,port,username,_=options
        self.state={"state":"connecting","ready":False,"host":host,"port":port,"username":username,"error":None}
        self.job=asyncio.create_task(self._connect(options,generation))
        return {"ok":True,"message":"Connection started; see SERVER status on HUD"}

    def _start(self,host,port,username,password):
        deps=self.root/".local/serverdeps"
        if deps.is_dir() and str(deps) not in sys.path:sys.path.insert(0,str(deps))
        import paramiko
        client=paramiko.SSHClient();known=self.root/".local/isaac-lab/ssh-known-hosts"
        known.parent.mkdir(parents=True,exist_ok=True)
        if known.exists():client.load_host_keys(str(known))
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy()) # Same accept-new policy as the existing launcher.
        try:
            client.connect(host,port=port,username=username,password=password,look_for_keys=False,allow_agent=False,
                           timeout=10,banner_timeout=10,auth_timeout=10)
            client.save_host_keys(str(known))
            transport=client.get_transport()
            transport.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            transport.set_keepalive(15)
            # No user-controlled shell interpolation. The existing idempotent
            # launcher leaves the model running after this connection closes.
            _,out,_=client.exec_command("cd /xufanghui/isaac-jev && .venv/bin/python scripts/start_policy_service.py",timeout=20)
            if out.channel.recv_exit_status()!=0:raise RuntimeError("Remote policy launcher failed")
            class Handler(socketserver.BaseRequestHandler):
                def handle(self):
                    channel=None
                    try:
                        channel=transport.open_channel("direct-tcpip",("127.0.0.1",19528),self.client_address,timeout=5)
                        if channel is None:return
                        while transport.is_active():
                            readable,_,_=select.select([self.request,channel],[],[],1)
                            if self.request in readable:
                                data=self.request.recv(65536)
                                if not data:break
                                channel.sendall(data)
                            if channel in readable:
                                data=channel.recv(65536)
                                if not data:break
                                self.request.sendall(data)
                    except (OSError,EOFError,paramiko.SSHException):pass
                    finally:
                        if channel is not None:channel.close()
            forwarder=ForwardServer(("127.0.0.1",self.local_port),Handler)
            threading.Thread(target=forwarder.serve_forever,name="isaac-ssh-forward",daemon=True).start()
            return client,forwarder
        except BaseException:
            client.close();raise

    @staticmethod
    def _close(client,forwarder):
        if forwarder:forwarder.shutdown();forwarder.server_close()
        if client:client.close()

    async def _connect(self,options,generation):
        host,port,username,password=options;options=None
        client=forwarder=None
        try:
            if await self.probe():
                if generation!=self.generation:return
                self.state.update(state="ready",ready=True,source="existing_tunnel",host=None,port=None,username=None,
                                  message="Existing local policy link reused; its SSH destination was not changed")
                return
            # Do not steal a listening port from an unhealthy external tunnel.
            with socket.socket() as candidate:candidate.bind(("127.0.0.1",self.local_port))
            if password is None:
                self.state["state"]="awaiting_password"
                password=await asyncio.to_thread(prompt_password)
                if not password:raise RuntimeError("Password entry cancelled")
            if generation!=self.generation:return
            self.state["state"]="connecting"
            client,forwarder=await asyncio.to_thread(self._start,host,port,username,password)
            password=None
            if generation!=self.generation:
                await asyncio.to_thread(self._close,client,forwarder);return
            self.client,self.forwarder=client,forwarder;client=forwarder=None
            self.state.update(state="warming_up",source="managed_tunnel",ssh_tcp_nodelay=True,local_tcp_nodelay=True)
            for _ in range(90):
                if generation!=self.generation:return
                ready=await self.probe()
                if generation!=self.generation:return
                if ready:self.state.update(state="ready",ready=True);return
                if not self.client.get_transport().is_active():raise ConnectionError("SSH disconnected")
                await asyncio.sleep(1)
            raise TimeoutError("Policy warmup deadline")
        except Exception as exc:
            password=None
            # Errors are deliberately classified; arbitrary server text and
            # credential-bearing payloads never enter HUD/status/recordings.
            name=type(exc).__name__
            messages={"AuthenticationException":"SSH authentication failed","BadHostKeyException":"SSH host key changed; verify server identity",
                      "ModuleNotFoundError":"Install project SSH dependencies using Setup_Game_Link.cmd",
                      "OSError":"Local policy port busy or network unavailable","RuntimeError":"Password cancelled or remote launcher failed",
                      "TimeoutError":"SSH/policy readiness timed out"}
            if generation==self.generation:
                self.state.update(state="error",ready=False,error=messages.get(name,"SSH connection failed ("+name+")"))
                await self._release()
            if client or forwarder:await asyncio.to_thread(self._close,client,forwarder)
        finally:password=None

    async def _release(self):
        client,forwarder=self.client,self.forwarder;self.client=self.forwarder=None
        if client or forwarder:await asyncio.to_thread(self._close,client,forwarder)

    async def disconnect(self):
        owned=bool(self.client);self.generation+=1;await self._release()
        self.state={"state":"disconnected","ready":False,"error":None}
        return {"ok":True,"message":"Managed tunnel closed; remote model remains running" if owned else "No managed tunnel; external SSH windows remain open"}

    async def refresh(self):
        if self.state.get("ready") and (self.client and not self.client.get_transport().is_active()):
            await self._release();self.state.update(state="disconnected",ready=False,error="SSH disconnected; run jev_connect again")
        if not (self.job and not self.job.done()) and time.monotonic()-self.last_probe>10:
            self.last_probe=time.monotonic()
            generation=self.generation
            ready=await self.probe()
            if generation!=self.generation:return
            if ready:self.state.update(state="ready",ready=True,source="managed_tunnel" if self.client else "existing_tunnel",error=None)
            elif self.state.get("ready"):
                self.state.update(state="policy_unavailable" if self.client else "disconnected",ready=False,error="Policy link unavailable; check SSH/server")

    async def close(self):
        await self.disconnect()
        if self.job and not self.job.done():
            # A worker may still be finishing authentication. Generation checks
            # close its result rather than abandoning an untracked SSH client.
            try:await asyncio.wait_for(asyncio.shield(self.job),1)
            except asyncio.TimeoutError:pass
