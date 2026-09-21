# Tram demo project

一个最小的"目标项目"，用于演示 Tram 治理闭环。运行：

```bash
./smoke.sh
```

流程：`tram init` → 人类批准范围基线 → fake agent 写入范围内文件（提交到 `tram/T-00x` 分支）→ 同一任务试图写 `docs/scratch.txt`（基线外）→ Intent Guard 拦截并生成 CR → 质量门跑真实 pytest → `tram status` / `tram replay` 查看状态与事件黑匣子。

- `plan-ok.json`：fake runner 的范围内写入计划（`mul.py`）
- `plan-bad.json`：基线外写入计划（`docs/scratch.txt`），用来演示拦截
