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
    def encode(self,texts):
        encoded=self.tokenizer(texts,padding=True,truncation=False,return_tensors="pt")
        if encoded["input_ids"].shape[1]>self.max_length:
            raise ValueError(f"Input has {encoded['input_ids'].shape[1]} tokens, budget {self.max_length}; shorten the feature prompt or retrain with a larger budget")
        encoded={k:v.to(self.device) for k,v in encoded.items() if k in {"input_ids","attention_mask"}}
        out=self.backbone(**encoded,use_cache=False,return_dict=True)
        positions=encoded["attention_mask"].sum(dim=1)-1
        rows=torch.arange(positions.shape[0],device=self.device)
        return out.last_hidden_state[rows,positions].float()
