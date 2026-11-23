"""GPU-free loopback echo endpoint for SSH-tunnel RTT measurements."""
import argparse
import asyncio
import json


async def serve(port):
    stop=asyncio.Event()
    async def client(reader,writer):
        try:
            while line:=await reader.readline():
                request=json.loads(line)
                shutdown=request.get("command")=="shutdown"
                response={"ok":True,"shutdown":True} if shutdown else {"ok":True,"pong":True}
                writer.write((json.dumps(response)+"\n").encode());await writer.drain()
                if shutdown:stop.set();break
        finally:writer.close();await writer.wait_closed()
    server=await asyncio.start_server(client,"127.0.0.1",port,limit=2*1024*1024)
    print(json.dumps({"ready":True,"port":port,"purpose":"network RTT only; no GPU allocation"}),flush=True)
    async with server:await stop.wait()
    server.close();await server.wait_closed()


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--port",type=int,default=19528)
    asyncio.run(serve(parser.parse_args().port))
