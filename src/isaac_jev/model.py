"""Optional Torch components: retain Qwen's pretrained text stack, train only small heads."""
import gc
import torch
from torch import nn

class CombatHead(nn.Module):
    def __init__(self, embedding_size, numeric_size, hidden_size=256, active_enabled=True):
        super().__init__()
        branch_size=hidden_size//2
        self.text=nn.Sequential(nn.LayerNorm(embedding_size),nn.Linear(embedding_size,branch_size),
                                nn.GELU(),nn.LayerNorm(branch_size))
        self.numeric=nn.Sequential(nn.Linear(numeric_size,branch_size),nn.GELU(),nn.LayerNorm(branch_size))
        self.shared=nn.Sequential(nn.Linear(branch_size*2,hidden_size),nn.GELU(),nn.Dropout(0.1))
        self.move=nn.Linear(hidden_size,9)
        self.shoot=nn.Linear(hidden_size,5)
        self.active=nn.Linear(hidden_size,1) if active_enabled else None

    def forward(self,embedding,numeric):
        hidden=self.shared(torch.cat((self.text(embedding.float()),self.numeric(numeric.float())),dim=-1))
        active=self.active(hidden).squeeze(-1) if self.active is not None else hidden.new_full((hidden.shape[0],),float("-inf"))
        return self.move(hidden),self.shoot(hidden),active


class HierarchicalCombatHead(CombatHead):
    def __init__(self, embedding_size, numeric_size, hidden_size=256, active_enabled=False):
        if active_enabled:raise ValueError('Experimental heads disable active items')
        super().__init__(embedding_size,numeric_size,hidden_size,False)
        self.move_gate=nn.Linear(hidden_size,2)
        self.move_direction=nn.Linear(hidden_size,8)
        del self.move

    def forward(self,embedding,numeric):
        hidden=self.shared(torch.cat((self.text(embedding.float()),self.numeric(numeric.float())),dim=-1))
        gate=self.move_gate(hidden).log_softmax(-1)
        directions=self.move_direction(hidden).log_softmax(-1)
        move=torch.cat((gate[:,:1],gate[:,1:]+directions),dim=-1)
        return move,self.shoot(hidden),hidden.new_full((hidden.shape[0],),float('-inf'))


class DecoupledCombatHead(nn.Module):
    def __init__(self,embedding_size,numeric_size,hidden_size=256,active_enabled=False):
        super().__init__()
        if active_enabled:raise ValueError('Experimental heads disable active items')
        def tower():
            branch=hidden_size//2
            return nn.ModuleDict({'text':nn.Sequential(nn.LayerNorm(embedding_size),nn.Linear(embedding_size,branch),nn.GELU(),nn.LayerNorm(branch)),
                'numeric':nn.Sequential(nn.Linear(numeric_size,branch),nn.GELU(),nn.LayerNorm(branch)),
                'shared':nn.Sequential(nn.Linear(hidden_size,hidden_size),nn.GELU(),nn.Dropout(.1))})
        self.move_tower=tower();self.shoot_tower=tower()
        self.move=nn.Linear(hidden_size,9);self.shoot=nn.Linear(hidden_size,5)

    def forward(self,embedding,numeric):
        def encode(t):return t['shared'](torch.cat((t['text'](embedding.float()),t['numeric'](numeric.float())),dim=-1))
        move=self.move(encode(self.move_tower));shoot=self.shoot(encode(self.shoot_tower))
        return move,shoot,move.new_full((move.shape[0],),float('-inf'))


class SemanticQualityHead(DecoupledCombatHead):
    """E08 towers with an action-conditioned, training-only ordinal auxiliary head."""
    def __init__(self,embedding_size,numeric_size,hidden_size=256,active_enabled=False):
        super().__init__(embedding_size,numeric_size,hidden_size,active_enabled)
        self.quality_action=nn.Embedding(45,16)
        self.quality=nn.Sequential(nn.Linear(hidden_size*2+16,128),nn.GELU(),nn.Linear(128,35))

    def quality_logits(self,embedding,numeric,actions):
        def encode(t):return t['shared'](torch.cat((t['text'](embedding.float()),t['numeric'](numeric.float())),dim=-1))
        hidden=torch.cat((encode(self.move_tower),encode(self.shoot_tower),self.quality_action(actions)),dim=-1)
        return self.quality(hidden).reshape(-1,7,5)


class TextOptionCombatHead(nn.Module):
    """One state encoding, cached text-option embeddings, one shared candidate scorer."""
    def __init__(self,embedding_size,numeric_size,hidden_size=256,active_enabled=False,candidate_indices=None):
        super().__init__()
        if active_enabled:raise ValueError('Text option heads disable active items')
        self.register_buffer('option_embeddings',torch.zeros(45,embedding_size))
        indices=candidate_indices or [[0]*12 for _ in range(45)]
        if len(indices)!=45 or any(len(row)!=12 for row in indices):raise ValueError('Invalid candidate feature indices')
        self.register_buffer('candidate_indices',torch.tensor(indices,dtype=torch.long),persistent=False)
        self.text=nn.Sequential(nn.LayerNorm(embedding_size),nn.Linear(embedding_size,128),nn.GELU(),nn.LayerNorm(128))
        self.numeric=nn.Sequential(nn.Linear(numeric_size,128),nn.GELU(),nn.LayerNorm(128))
        self.context=nn.Sequential(nn.Linear(256,256),nn.GELU(),nn.Dropout(.1))
        self.option=nn.Sequential(nn.LayerNorm(embedding_size),nn.Linear(embedding_size,64),nn.GELU(),nn.LayerNorm(64))
        self.option_facts=nn.Sequential(nn.Linear(12,32),nn.GELU(),nn.LayerNorm(32))
        self.scorer=nn.Sequential(nn.Linear(352,256),nn.GELU(),nn.Linear(256,64),nn.GELU(),nn.Linear(64,1))
        self.quality=nn.Sequential(nn.Linear(352,128),nn.GELU(),nn.Linear(128,35))

    def candidate_hidden(self,embedding,numeric,action_ids=None):
        ids=torch.arange(45,device=numeric.device) if action_ids is None else torch.as_tensor(action_ids,device=numeric.device,dtype=torch.long)
        context=self.context(torch.cat((self.text(embedding.float()),self.numeric(numeric.float())),dim=-1))
        texts=self.option(self.option_embeddings[ids].float()).unsqueeze(0).expand(context.shape[0],-1,-1)
        facts=self.option_facts(numeric[:,self.candidate_indices[ids]].float())
        return torch.cat((context.unsqueeze(1).expand(-1,len(ids),-1),texts,facts),dim=-1)

    def score_candidates(self,embedding,numeric,action_ids=None):
        return self.scorer(self.candidate_hidden(embedding,numeric,action_ids)).squeeze(-1)

    def forward(self,embedding,numeric):
        joint=self.score_candidates(embedding,numeric)
        # Explicit joint logits; decode_actions handles these without independent argmaxes.
        return joint,None,joint.new_full((joint.shape[0],),float('-inf'))

    def quality_logits(self,embedding,numeric,actions):
        hidden=self.candidate_hidden(embedding,numeric)
        selected=hidden[torch.arange(len(actions),device=actions.device),actions]
        return self.quality(selected).reshape(-1,7,5)


def load_extended_numeric_state(head,state,old_numeric_size):
    """Preserve the existing actor and zero only newly added numerical columns."""
    target=head.state_dict()
    for name,value in state.items():
        if name not in target:raise ValueError('Initialization contains an unknown parameter')
        if target[name].shape==value.shape:target[name]=value
        elif name.endswith('numeric.0.weight') and value.ndim==2 and value.shape[1]==old_numeric_size and target[name].shape[0]==value.shape[0] and target[name].shape[1]>old_numeric_size:
            target[name]=torch.zeros_like(target[name]);target[name][:,:old_numeric_size]=value
        else:raise ValueError('Unsupported initialization shape change: '+name)
    head.load_state_dict(target)


def head_from_config(config):
    classes={'two-tower-factorized-v2':CombatHead,'two-tower-move-gate-v1':HierarchicalCombatHead,
             'separate-move-shoot-towers-v1':DecoupledCombatHead,
             'separate-quality-towers-v7':SemanticQualityHead,'shared-text-option-scorer-v7':TextOptionCombatHead}
    architecture=config.get('head_architecture','two-tower-factorized-v2')
    if architecture not in classes:raise ValueError('Unknown decision head architecture')
    kwargs={'active_enabled':config.get('active_head_present',False)}
    if architecture=='shared-text-option-scorer-v7':kwargs['candidate_indices']=config.get('candidate_numeric_indices')
    return classes[architecture](config['embedding_size'],config['numeric_size'],config.get('head_hidden_size',256),**kwargs)


def decode_actions(head,move_logits,shoot_logits):
    if isinstance(head,TextOptionCombatHead):
        action=move_logits.argmax(-1)
        return action//5,action%5
    if isinstance(head,HierarchicalCombatHead):
        moving=move_logits[:,1:].logsumexp(-1)>move_logits[:,0]
        move=torch.where(moving,move_logits[:,1:].argmax(-1)+1,torch.zeros_like(move_logits[:,0],dtype=torch.long))
    else:move=move_logits.argmax(-1)
    return move,shoot_logits.argmax(-1)

class FrozenQwen:
    def __init__(self,model_id,revision="main",device="cuda",dtype="bfloat16",max_length=1024):
        from transformers import AutoModel,AutoTokenizer
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; install a CUDA Torch build")
        self.device=device;self.max_length=max_length
        chosen={"bfloat16":torch.bfloat16,"float16":torch.float16,"float32":torch.float32}[dtype]
        if device=="cpu":chosen=torch.float32
        # Load on CPU first; only transfer the text stack to the GPU. AutoModel omits vocabulary logits.
        full=AutoModel.from_pretrained(model_id,revision=revision,torch_dtype=chosen,
                                      attn_implementation="sdpa",trust_remote_code=False)
        self.revision=getattr(full.config,"_commit_hash",None) or revision
        text=getattr(full,"language_model",None)
        if text is None:
            nested=getattr(full,"model",None)
            text=getattr(nested,"language_model",None) if nested is not None else None
        if text is None:
            raise RuntimeError("Expected Qwen3.5 pretrained language_model; refusing to use a randomly initialized substitute")
        self.backbone=text
        self.embedding_size=text.config.hidden_size
        del full;gc.collect()
        self.backbone.to(device).eval().requires_grad_(False)
        self.tokenizer=AutoTokenizer.from_pretrained(model_id,revision=self.revision,trust_remote_code=False)
        self.tokenizer.padding_side="right"
        if self.tokenizer.pad_token_id is None:self.tokenizer.pad_token=self.tokenizer.eos_token

    @torch.no_grad()
    def encode(self,texts,*,pad_to_multiple_of=None):
        encoded=self.tokenizer(texts,padding=True,truncation=False,return_tensors="pt",
                               pad_to_multiple_of=pad_to_multiple_of)
        if encoded["input_ids"].shape[1]>self.max_length:
            raise ValueError(f"Input has {encoded['input_ids'].shape[1]} tokens, budget {self.max_length}; shorten the feature prompt or retrain with a larger budget")
        encoded={k:v.to(self.device) for k,v in encoded.items() if k in {"input_ids","attention_mask"}}
        out=self.backbone(**encoded,use_cache=False,return_dict=True)
        positions=encoded["attention_mask"].sum(dim=1)-1
        rows=torch.arange(positions.shape[0],device=self.device)
        return out.last_hidden_state[rows,positions].float()
