"""Training-only access to the already warm loopback Qwen encoder; no actions are issued."""
import base64
import json
import socket
import time


class PolicyFeatureEncoder:
    def __init__(self,config,port=19528):
        from transformers import AutoTokenizer
        self.config=config;self.port=port
        reply=self.request({'command':'describe'})
        actual=reply['config']
        if actual['revision']!=config['revision'] or actual['embedding_size']!=config['embedding_size']:
            raise ValueError('Existing encoder differs from the source model')
        self.backend=reply['backend']['checkpoint'];self.embedding_size=actual['embedding_size'];self.revision=actual['revision']
        self.tokenizer=AutoTokenizer.from_pretrained(config['model'],revision=config['revision'],trust_remote_code=False)
    def request(self,payload):
        with socket.create_connection(('127.0.0.1',self.port),timeout=20) as sock:
            sock.sendall((json.dumps(payload,separators=(',',':'))+'\n').encode())
            result=json.loads(sock.makefile('rb').readline())
        if not result.get('ok'):raise RuntimeError(result.get('error','Feature RPC failed'))
        return result
    def encode(self,texts,*,pad_to_multiple_of=None):
        import numpy as np
        import torch
        output=[]
        for text in texts:
            for attempt in range(100):
                try:
                    reply=self.request({'command':'encode_features','texts':[text],'model_revision':self.revision})
                    break
                except RuntimeError as error:
                    if 'GPU busy' not in str(error) or attempt==99:raise
                    time.sleep(.02)
            if reply['model_revision']!=self.revision or reply['embedding_size']!=self.embedding_size or reply.get('dtype')!='float16':
                raise ValueError('Feature response contract differs')
            row=np.frombuffer(base64.b64decode(reply['features_base64']),dtype=np.float16).copy()
            if row.shape!=(self.embedding_size,) or not np.isfinite(row).all():raise ValueError('Invalid frozen feature response')
            output.append(row)
        return torch.from_numpy(np.stack(output))
