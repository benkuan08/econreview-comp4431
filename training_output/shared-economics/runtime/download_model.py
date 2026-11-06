import json,time
from pathlib import Path
from huggingface_hub import snapshot_download
info=json.loads(Path('model_info.json').read_text())
print('DOWNLOADING',info,flush=True)
t=time.time()
p=snapshot_download(repo_id=info['id'],revision=info['sha'],local_dir='models/Qwen3.5-9B-Base',max_workers=8,allow_patterns=['*.json','*.safetensors','*.jinja','*.txt','*.model'])
index=json.loads((Path(p)/'model.safetensors.index.json').read_text())
files=set(index['weight_map'].values())
assert all((Path(p)/f).is_file() and (Path(p)/f).stat().st_size>0 for f in files)
print('DOWNLOAD_COMPLETE',len(files),'weight_shards','seconds',round(time.time()-t,1),flush=True)
Path('runtime/download.ok').write_text(json.dumps({'path':p,'revision':info['sha'],'shards':len(files)}))
