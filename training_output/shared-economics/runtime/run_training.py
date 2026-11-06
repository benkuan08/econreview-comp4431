"""One-off two-step smoke check, then a fresh full run on the same loaded model."""
import gc,json,random,sys,time
from pathlib import Path
import numpy as np
import torch
from transformers import Trainer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'training'))
STATUS=ROOT/'runtime/status.json'
def status(stage,**values):
 STATUS.write_text(json.dumps({'stage':stage,'time':time.strftime('%Y-%m-%d %H:%M:%S'),**values},indent=2))
 print('STAGE',stage,values,flush=True)
original_train=Trainer.train

def smoke_then_train(trainer,*args,**kwargs):
 status('smoke_test')
 model=trainer.model
 model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
 params=[p for p in model.parameters() if p.requires_grad]
 initial={n:p.detach().cpu().clone() for n,p in model.named_parameters() if p.requires_grad}
 rng=(random.getstate(),np.random.get_state(),torch.get_rng_state(),torch.cuda.get_rng_state_all())
 optimizer=torch.optim.AdamW(params,lr=trainer.args.learning_rate,weight_decay=trainer.args.weight_decay)
 micro=trainer.args.per_device_train_batch_size
 longest=sorted(trainer.train_dataset,key=lambda x:len(x['input_ids']),reverse=True)[:2*micro]
 batches=[longest[i:i+micro] for i in range(0,len(longest),micro)]
 torch.cuda.reset_peak_memory_stats()
 start=time.time()
 losses=[]
 for step,features in enumerate(batches,1):
  model.train()
  batch={k:v.to('cuda') for k,v in trainer.data_collator(features).items()}
  optimizer.zero_grad(set_to_none=True)
  with torch.autocast('cuda',dtype=torch.bfloat16):
   loss=model(**batch).loss
  if not torch.isfinite(loss): raise RuntimeError('Non-finite smoke loss')
  loss.backward()
  norm=torch.nn.utils.clip_grad_norm_(params,trainer.args.max_grad_norm)
  if not torch.isfinite(norm) or float(norm)==0: raise RuntimeError('Invalid or zero smoke gradients')
  optimizer.step()
  torch.cuda.synchronize()
  losses.append(float(loss.detach()))
  print('SMOKE_STEP',step,'tokens',max(len(f['input_ids']) for f in features),'micro_batch',len(features),'loss',losses[-1],'grad_norm',float(norm),'elapsed',round(time.time()-start,1),flush=True)
  del loss,batch,norm
 changed=any(not torch.equal(p.detach().cpu(),initial[n]) for n,p in model.named_parameters() if p.requires_grad)
 if not changed: raise RuntimeError('Smoke optimizer did not update adapter weights')
 feature=max(trainer.eval_dataset,key=lambda x:len(x['input_ids']))
 batch={k:v.to('cuda') for k,v in trainer.data_collator([feature]).items()}
 model.eval()
 with torch.no_grad(),torch.autocast('cuda',dtype=torch.bfloat16):
  validation_loss=model(**batch).loss
 if not torch.isfinite(validation_loss): raise RuntimeError('Non-finite smoke validation loss')
 smoke={'passed':True,'steps':2,'micro_batch':micro,'training_losses':losses,'validation_loss':float(validation_loss),'max_train_tokens':len(longest[0]['input_ids']),'max_validation_tokens':len(feature['input_ids']),'peak_allocated_GiB':torch.cuda.max_memory_allocated()/2**30,'peak_reserved_GiB':torch.cuda.max_memory_reserved()/2**30,'elapsed_seconds':round(time.time()-start,1),'adapter_weights_updated':changed,'full_run_starts_from_original_adapter':True}
 (Path(trainer.args.output_dir)/'smoke_result.json').write_text(json.dumps(smoke,indent=2))
 print('SMOKE_PASSED',json.dumps(smoke),flush=True)
 # The smoke check must not contribute extra optimization to the real experiment.
 with torch.no_grad():
  for n,p in model.named_parameters():
   if p.requires_grad: p.copy_(initial[n].to(device=p.device,dtype=p.dtype))
 model.zero_grad(set_to_none=True)
 del initial,optimizer,batch,validation_loss,params
 gc.collect()
 torch.cuda.empty_cache()
 random.setstate(rng[0]);np.random.set_state(rng[1]);torch.set_rng_state(rng[2]);torch.cuda.set_rng_state_all(rng[3])
 torch.cuda.reset_peak_memory_stats()
 model.train()
 status('training',smoke_passed=True)
 return original_train(trainer,*args,**kwargs)

Trainer.train=smoke_then_train
try:
 status('loading_model')
 import train_lora
 train_lora.main()
 status('complete')
except BaseException as error:
 status('failed',error_type=type(error).__name__,error=str(error)[:1200])
 raise
