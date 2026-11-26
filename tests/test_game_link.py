import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import socket
import sys
import threading
import unittest
from unittest.mock import patch

from isaac_jev.bridge import BridgeServer
from isaac_jev.mock import sample_frame
from isaac_jev.presentation import AgentPresentation
from isaac_jev.protocol import encode, decode
from isaac_jev.recording import iter_messages
from isaac_jev.server_link import ServerLink, ForwardServer, connection_options
from isaac_jev.skill_service import ToolRegistry

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.local/serverdeps'))
try:
    import paramiko
except ImportError:
    paramiko=None


class LinkValidationTests(unittest.TestCase):
    def test_forwarder_disables_nagle_on_every_accepted_socket(self):
        class Handler:
            pass
        with ForwardServer(('127.0.0.1',0),Handler) as server:
            with socket.create_connection(server.server_address,timeout=2) as client:
                accepted,_=server.get_request()
                try:self.assertEqual(accepted.getsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY),1)
                finally:accepted.close()

    def test_connection_fields_are_data_not_shell_commands(self):
        self.assertEqual(connection_options({"host":"10.28.0.22","password":"test $() password"}),
                         ("10.28.0.22",31203,"root","test $() password"))
        for data in ({"host":"10.0.0.1; calc"},{"host":"https://host"},{"host":"10.0.0.1","port":True},
                     {"host":"10.0.0.1","username":"root; command\n"}):
            with self.assertRaises(ValueError):connection_options(data)


class GameLinkTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipIf(paramiko is None,'Install project link dependencies for SSH integration test')
    async def test_actual_password_auth_and_bidirectional_loopback_forwarding(self):
        commands=[];destinations=[];transport_holder=[]
        class SSHServer(paramiko.ServerInterface):
            def check_auth_password(self,username,password):
                return paramiko.AUTH_SUCCESSFUL if (username,password)==('root','dummy') else paramiko.AUTH_FAILED
            def get_allowed_auths(self,username):return 'password'
            def check_channel_request(self,kind,channel_id):return paramiko.OPEN_SUCCEEDED
            def check_channel_direct_tcpip_request(self,channel_id,origin,destination):
                destinations.append(destination);return paramiko.OPEN_SUCCEEDED
            def check_channel_exec_request(self,channel,command):
                commands.append(command)
                def finish():channel.send_exit_status(0)
                threading.Thread(target=finish,daemon=True).start();return True
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(10)
        stop=threading.Event();key=paramiko.RSAKey.generate(2048)
        def server():
            conn,_=listener.accept();transport=paramiko.Transport(conn);transport_holder.append(transport)
            transport.add_server_key(key);transport.start_server(server=SSHServer())
            while not stop.is_set() and transport.is_active():
                channel=transport.accept(.1)
                if not channel:continue
                def echo(c):
                    try:
                        while data:=c.recv(4096):c.sendall(data)
                    finally:c.close()
                threading.Thread(target=echo,args=(channel,),daemon=True).start()
        thread=threading.Thread(target=server,daemon=True);thread.start()
        with tempfile.TemporaryDirectory() as tmp:
            link=ServerLink(tmp,local_port=0);client=forwarder=None
            try:
                client,forwarder=await asyncio.to_thread(link._start,'127.0.0.1',listener.getsockname()[1],'root','dummy')
                self.assertEqual(client.get_transport().sock.getsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY),1)
                def round_trip():
                    with socket.create_connection(forwarder.server_address,timeout=5) as local:
                        local.sendall(b'ping\n');return local.recv(4096)
                self.assertEqual(await asyncio.to_thread(round_trip),b'ping\n')
                self.assertEqual(destinations,[('127.0.0.1',19528)])
                self.assertEqual(commands,[b'cd /xufanghui/isaac-jev && .venv/bin/python scripts/start_policy_service.py'])
                known=Path(tmp)/'.local/isaac-lab/ssh-known-hosts'
                self.assertTrue(known.exists());self.assertNotIn('dummy',known.read_text())
            finally:
                await asyncio.to_thread(link._close,client,forwarder)
                stop.set()
                for transport in transport_holder:transport.close()
                listener.close();thread.join(timeout=1)

    async def test_mod_management_credentials_never_reach_recordings(self):
        class FakeLink:
            def begin(self,payload):return {"ok":True,"message":"Connection started"}
            def status(self):return {"state":"connecting"}
            async def close(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            hub=BridgeServer(port=0,record_root=Path(tmp),recording_enabled=False,server_link=FakeLink())
            await hub.start()
            reader,writer=await asyncio.open_connection('127.0.0.1',hub.port)
            try:
                writer.write(encode({"protocol_version":1,"type":"hello","client":"isaac_mod"}));await writer.drain()
                self.assertFalse(decode(await reader.readline())["recording"])
                async def management(**fields):
                    writer.write(encode({"protocol_version":1,"type":"management_request",**fields}));await writer.drain()
                    reply=decode(await reader.readline())
                    await reader.readline();await reader.readline()
                    return reply
                self.assertTrue((await management(operation="recording",enabled=True))["ok"])
                first=hub.recorder.path
                await management(operation="server_connect",host="127.0.0.1",password="UNIQUE_DUMMY_SECRET")
                writer.write(encode(sample_frame(0)));await writer.drain();await asyncio.sleep(.03)
                self.assertTrue((await management(operation="recording",enabled=False))["ok"])
                messages=list(iter_messages(first))
                self.assertTrue(any(m.get("type")=="frame" for m in messages))
                self.assertNotIn("UNIQUE_DUMMY_SECRET",json.dumps(messages))
                self.assertFalse(any(m.get("type")=="management_request" for m in messages))
                self.assertIsNone(hub.control_lease);self.assertFalse(hub.recording_enabled)
                self.assertTrue(json.loads((first/'session.json').read_text())["closed_at_unix"])
            finally:
                writer.close();await writer.wait_closed();await hub.close()

    async def test_start_recording_is_idempotent_and_display_cannot_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub=BridgeServer(port=0,record_root=Path(tmp))
            first=hub.recorder.path
            self.assertTrue((await hub.management("recording",{"enabled":True}))["ok"])
            self.assertEqual(first,hub.recorder.path)
            await hub.control({"command":"set_presentation","display":{"agent_state":"thinking","summary":"Choosing room","password":"DUMMY"}})
            self.assertIsNone(hub.control_lease)
            self.assertNotIn("password",hub.presentation_message()["display"])
            with patch('isaac_jev.bridge.time.monotonic',return_value=hub.presentation_received+4):
                self.assertEqual(hub.presentation_message()["display"]["agent_state"],"offline")
            await hub.close()

    async def test_background_ssh_readiness_and_duplicate_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            link=ServerLink(tmp,local_port=0);calls=[]
            async def probe():calls.append(True);return len(calls)>1
            fake=SimpleNamespace(get_transport=lambda:SimpleNamespace(is_active=lambda:True))
            closed=[];link.probe=probe
            link._start=lambda *args:(fake,None)
            link._close=lambda c,f:closed.append(c)
            self.assertTrue(link.begin({"host":"127.0.0.1","password":"dummy"})["ok"])
            self.assertFalse(link.begin({"host":"127.0.0.1","password":"dummy"})["ok"])
            await link.job
            self.assertTrue(link.status()["ready"]);self.assertNotIn('password',link.status())
            await link.disconnect();self.assertEqual(closed,[fake]);self.assertFalse(link.status()["ready"])

    async def test_connection_errors_do_not_echo_passwords(self):
        with tempfile.TemporaryDirectory() as tmp:
            link=ServerLink(tmp,local_port=0)
            async def probe():return False
            def fail(*args):raise RuntimeError('DUMMY_SECRET arbitrary server output')
            link.probe=probe;link._start=fail
            link.begin({"host":"127.0.0.1","password":"DUMMY_SECRET"});await link.job
            self.assertEqual(link.status()["state"],"error")
            self.assertNotIn('DUMMY_SECRET',json.dumps(link.status()))
            self.assertEqual(list(Path(tmp).iterdir()),[])

    async def test_disconnect_during_readiness_cannot_restore_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            link=ServerLink(tmp,local_port=0);waiting=asyncio.Event();gate=asyncio.Event();attempts=0
            async def probe():
                nonlocal attempts
                attempts+=1
                if attempts==1:return False
                waiting.set();await gate.wait();return True
            fake=SimpleNamespace(get_transport=lambda:SimpleNamespace(is_active=lambda:True))
            link.probe=probe;link._start=lambda *args:(fake,None);link._close=lambda *args:None
            link.begin({"host":"127.0.0.1","password":"dummy"})
            await asyncio.wait_for(waiting.wait(),2);await link.disconnect();gate.set();await link.job
            self.assertEqual(link.status()["state"],"disconnected")
            self.assertFalse(link.status()["ready"])

    async def test_tools_and_explicit_thinking_are_distinct_and_cancelled_calls_end(self):
        display=AgentPresentation();registry=ToolRegistry(display);gate=asyncio.Event()
        async def wait():await gate.wait();return {}
        registry.register('query','query',{},[],wait)
        display.set('thinking','Choosing next room')
        self.assertEqual(display.snapshot()['agent_state'],'thinking')
        task=asyncio.create_task(registry.call('query',{}));await asyncio.sleep(0)
        self.assertEqual(display.snapshot()['tool'],'query')
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(display.calls,{})
        self.assertEqual(display.snapshot()['agent_state'],'thinking')


if __name__=='__main__':unittest.main()
