from pathlib import Path
import json,shutil,hashlib
W=Path('/mnt/data/home/sunyujie/robot_manipulation');O=W/'experiments/reproduction_inventory_hot_drink_20261025';P=O/'package_staging';A=W/'experiments/hot_drink_long_horizon_ablation_20261024'
def copy(a,b):b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(a,b)
for f in ['preflight.py','preflight.log','removed_prompt_requirement.txt','prelaunch_fix.json','README.md']:
 copy(A/f,P/'experiment_records/ablation'/f)
# Small metadata outside original frozen task inputs, labelled current recovery snapshot.
overlay=W/'experiments/hot_drink_skill_transfer_20261021_frozen/dataset_overlay';meta=[]
for rel in ['behavior-1k-assets/metadata','2026-challenge-task-instances/metadata']:
 for src in (overlay/rel).iterdir():
  if src.is_file():
   dst=P/'asset_inventory/current_metadata'/rel/src.name;copy(src,dst);meta.append({'original':str(src.resolve()),'package':str(dst.relative_to(P)),'sha256_current':hashlib.sha256(src.read_bytes()).hexdigest(),'historical_frozen':src.name=='available_tasks.yaml'})
(P/'asset_inventory/current_metadata_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
validation=P/'validation';validation.mkdir(exist_ok=True)
(validation/'audit_regression_check.py').write_text('''"""CPU-only check for the actual rejected-act audit crash; no simulator."""
from pathlib import Path
import sys,importlib.util
p=Path(__file__).resolve().parents[1]/"runtime/without_long_horizon"
sys.path.insert(0,str(p/"src"))
spec=importlib.util.spec_from_file_location("audit_check",p/"scripts/audit_demo_trial.py")
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
call={"kind":"tool_call","name":"act","arguments":{"outcome":"blocked"}}
failed={"kind":"tool_result","name":"act","result":{"ok":False}}
r=a.replay_high_level_actions([call,failed],[])
assert r["committed_actions"]==0 and not r["evaluation"]["task_success"]
try:a.replay_high_level_actions([call,{**failed,"result":{"ok":True}}],[])
except ValueError:pass
else:raise AssertionError("Missing primitive on successful action was accepted")
print("PASS: rejected malformed action does not commit; malformed successful action is rejected")
''')
(validation/'recompute_recorded_table.py').write_text('''"""Recompute the six-trial table from recorded final evaluations, not a new run."""
from pathlib import Path
import json,statistics
p=Path(__file__).resolve().parents[1];rows=[]
for arm in ["with_long_horizon","without_long_horizon"]:
 for seed in [0,11,22]:
  root=p/"evidence_metadata"/(arm+"_s"+str(seed))
  run=json.loads((root/"run/run.json").read_text());audit=json.loads((root/"demo_audit.json").read_text())
  rows.append(dict(arm=arm,seed=seed,success=audit["semantic_audit"]["evaluation"]["task_success"],actions=run["actions"]))
for arm in ["with_long_horizon","without_long_horizon"]:
 a=[r for r in rows if r["arm"]==arm];print(arm,"success",sum(x["success"] for x in a),"/",len(a),"mean_actions",statistics.mean(x["actions"] for x in a))
assert sum(r["success"] for r in rows if r["arm"]=="with_long_horizon")==3
assert sum(r["success"] for r in rows if r["arm"]=="without_long_horizon")==0
''')
(P/'README.md').write_text('''# 热饮长程skill对照：复现材料暂存包\n\n这是逐项清点生成的代码/配置/轻量证据包，尚不是通过干净机器验证的可运行发行版。目录外的INVENTORY.json、SOURCE_LINEAGE.json和RAW_EVIDENCE_INVENTORY.json说明范围与缺口。\n\n主结论范围：完整五skill历史组3个seed，与其他四skill不变、移除长程skill的新组三个seed。不是全部skill关闭实验；不是原生水粒子物理任务或官方成绩。\n\n- runtime/：两组实际冻结的src、scripts、skills、tests和pyproject，分别254/252文件；未修改历史字节。完整src必须保留，不能只摘执行器。\n- upstream/：BEHAVIOR-1K基准commit加当前17文件修改patch、5个未跟踪文件；补丁应用及内容哈希已验证。历史未保存上游工作树哈希，当前捕获不冒充历史认证。\n- task/、dataset_overlay/、configured_scene/：实际任务、场景模板、实例、目录及运行时修复后的场景。\n- asset_inventory/：大型外部资产、模型候选路径、版本及当前小地图/元数据；没有复制模型、材质、纹理、机器人和全部系统资产。不得上传许可证密钥。\n- configs/：原始启动计划（含本机绝对路径，需迁移）、六场真实提示词、工具列表、启动开关和环境读取点。\n- environment/：六场记录一致的298项包版本及客户端版本。不是已通过安装的依赖锁。\n- evidence_metadata/、results/：六场原始评分、审计、运行配置和对照结果；原始失败标志保留。\n- validation/：纯CPU审计缺primitive回归，及从已记录评分重算表格；不等同物理重跑或从完整日志独立重放评分。\n- documentation_current/：当前研究日志，仅供追溯；含较新杂货修复，不代表这些修复都进入本轮。\n\n离线核对（无GPU）：\n\n```bash\npython3 validation/audit_regression_check.py\npython3 validation/recompute_recorded_table.py\n```\n\n发布前仍需补齐：历史上游patch一致性说明、外部资产及哈希、明确环境和安装方案、移除绝对路径的启动器、六场大体积证据的公开存储与脱敏、干净机器验收。父目录中verify_staging.py只验证当前暂存包完整性，不能认证这些缺口已解决。\n''')
(O/'verify_staging.py').write_text('''"""Verify staged files; publication readiness is a separate explicit condition."""
from pathlib import Path
import json,hashlib,sys
p=Path(__file__).resolve().parent;m=json.loads((p/"STAGING_MANIFEST.json").read_text());bad=[]
for f,row in m["files"].items():
 q=p/"package_staging"/f
 if not q.is_file() or hashlib.sha256(q.read_bytes()).hexdigest()!=row["sha256"]:bad.append(f)
if bad:print("FAIL",bad);raise SystemExit(1)
print("PASS",len(m["files"]),"staged files match their hashes")
i=json.loads((p/"INVENTORY.json").read_text());print("publication_ready:",i["publication_ready"])
if "--require-publish-ready" in sys.argv and not i["publication_ready"]:raise SystemExit(2)
''')
files={str(f.relative_to(P)):{'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'bytes':f.stat().st_size} for f in P.rglob('*') if f.is_file()};(O/'STAGING_MANIFEST.json').write_text(json.dumps({'files':files,'total_files':len(files),'total_bytes':sum(x['bytes'] for x in files.values())},indent=2)+'\n')
print(len(files),sum(x['bytes'] for x in files.values()))
