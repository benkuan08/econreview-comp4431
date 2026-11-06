"""Resume official ModelScope files and verify the pinned Hugging Face SHA256s."""
import concurrent.futures,hashlib,json,os,threading,time,urllib.request,urllib.parse
from pathlib import Path
ROOT=Path('/root/autodl-tmp/COMP4431').resolve()
DEST=ROOT/'models/Qwen3.5-9B-Base'
MANIFEST=json.loads((ROOT/'runtime/weight_manifest.json').read_text())
CHUNK=32*1024*1024
started=time.time(); lock=threading.Lock(); done=0; initial=0; last_report=0
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  while b:=f.read(16*1024*1024): h.update(b)
 return h.hexdigest()

def progress(force=False):
 global last_report
 now=time.time()
 if force or now-last_report>=5:
  last_report=now
  value={'downloaded_bytes':done,'total_bytes':sum(x['size'] for x in MANIFEST.values()),'new_bytes_per_second':max(0,done-initial)/max(1,now-started),'elapsed_seconds':round(now-started,1),'time':now}
  (ROOT/'runtime/download_progress.json').write_text(json.dumps(value,indent=2))
  print('PROGRESS',json.dumps(value),flush=True)

jobs=[]; temporary={}
for name,meta in MANIFEST.items():
 target=DEST/name
 if target.exists() and target.stat().st_size==meta['size'] and digest(target)==meta['sha256']:
  print('REUSED_VERIFIED',name,meta['size'],flush=True); done+=meta['size']; continue
 prefix_files=[p for p in (DEST/'.cache/huggingface/download').glob('*.incomplete') if meta['sha256'] in p.name and p.stat().st_size<=meta['size']]
 source=max(prefix_files,key=lambda p:p.stat().st_size) if prefix_files else None
 temp=DEST/(name+'.parallel')
 if temp.exists(): raise RuntimeError('A previous parallel run needs explicit recovery')
 if source: os.link(source,temp)
 else: temp.touch()
 prefix=(temp.stat().st_size//CHUNK)*CHUNK
 fd=os.open(temp,os.O_RDWR)
 os.ftruncate(fd,meta['size'])
 url='https://modelscope.cn/api/v1/models/Qwen/Qwen3.5-9B-Base/repo?Revision=master&FilePath='+urllib.parse.quote_plus(name)
 temporary[name]=(temp,fd)
 done+=prefix
 for start in range(prefix,meta['size'],CHUNK): jobs.append((name,fd,url,start,min(start+CHUNK,meta['size'])-1))
 print('RESUME_PREFIX',name,prefix,flush=True)
initial=done
progress(True)

def fetch(job):
 global done
 name,fd,url,start,end=job
 for attempt in range(5):
  try:
   req=urllib.request.Request(url,headers={'Range':f'bytes={start}-{end}','User-Agent':'modelscope-training-download'})
   with opener.open(req,timeout=45) as response:
    expected=f'bytes {start}-{end}/{MANIFEST[name]["size"]}'
    if response.status!=206 or response.headers.get('Content-Range')!=expected:
     raise RuntimeError('Unexpected range response for '+name)
    offset=start
    while offset<=end:
     data=response.read(min(2*1024*1024,end-offset+1))
     if not data: raise RuntimeError('Incomplete range response')
     written=0
     while written<len(data): written+=os.pwrite(fd,data[written:],offset+written)
     offset+=len(data)
   with lock:
    done+=end-start+1
    progress()
   return
  except Exception as error:
   if attempt==4: raise
   print('RANGE_RETRY',name,start,type(error).__name__,attempt+1,flush=True)
   time.sleep(attempt+1)

# Interleave shards so no single large file monopolizes the first requests.
jobs.sort(key=lambda job:(job[3],job[0]))
with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
 for _ in pool.map(fetch,jobs): pass
for name,(temp,fd) in temporary.items():
 os.fsync(fd);os.close(fd)
 actual=digest(temp)
 if actual!=MANIFEST[name]['sha256']: raise RuntimeError('Pinned SHA256 mismatch: '+name)
 temp.replace(DEST/name)
 print('SHA256_VERIFIED',name,actual,flush=True)
progress(True)
info=json.loads((ROOT/'model_info.json').read_text())
(ROOT/'runtime/download.ok').write_text(json.dumps({'path':str(DEST),'revision':info['sha'],'sha256_verified':True,'shards':len(MANIFEST)},indent=2))
print('DOWNLOAD_COMPLETE_AND_VERIFIED',round(time.time()-started,1),flush=True)
