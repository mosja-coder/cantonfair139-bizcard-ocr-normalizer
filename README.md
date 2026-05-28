# cantonfair139-bizcard-ocr-normalizer

第139届广交会名片 OCR 流水线（火山 OCR + DeepSeek 结构化）。

火山 OCR（链路 A）+ DeepSeek 结构化（链路 B）。**全量已跑完**（2026-05-27）。

---

## 项目结构

```
cantonfair139-bizcard-ocr-normalizer/
├── README.md
├── requirements.txt
├── .env.example
├── scripts/                 # 一键入口（薄封装）
│   ├── run_ocr.sh           # 链路 A：批量 OCR
│   ├── run_deepseek_kp.sh   # 链路 B：DeepSeek 后处理
│   └── compare_ab.sh        # A/B 对比报告
├── src/                     # Python 实现
│   ├── paths.py             # 数据目录约定（统一路径）
│   ├── partitions.py        # 12 分区配置
│   ├── checkpoint.py        # OCR 页级断点
│   ├── run_pipeline.py      # 链路 A 入口
│   ├── deepseek_kp_pipeline.py
│   ├── compare_ab_pipeline.py
│   └── merge_phone_exports.py
└── data/
    ├── input/               # 全部 12 份 PDF（唯一输入目录）
    ├── output/              # 12 个 ocr_run_* 中间结果
    ├── deliverables/        # 最终 Excel 交付物
    └── cache/deepseek/      # B 链路断点缓存（可删后重跑 LLM）
```

### `src` 与 `scripts`

| 目录 | 作用 |
|------|------|
| **src/** | 业务逻辑与可复用模块 |
| **scripts/** | 激活 venv、调用 `src` 下脚本，适合日常一键执行 |

---

## 数据目录说明

| 路径 | 内容 |
|------|------|
| `data/input/` | 12 份扫描 PDF，**不要再按「1期/2期」分子文件夹** |
| `data/output/ocr_run_*` | 每分区 OCR + 名片拆分 + 手机号清洗的中间产物 |
| `data/deliverables/` | 对外交付的 xlsx（汇总、DeepSeek、A/B 对比报告、SEM 模板） |
| `data/cache/deepseek/` | `checkpoint.json` + `llm_results.jsonl`（仅 B 链路） |

---

## 双链路与交付物

| 文件 | 链路 | 说明 |
|------|------|------|
| `广交会139届_名片手机号汇总.xlsx` | A | 规则全量，9470 手机号行 |
| `广交会139届_名片手机号汇总_dsv4flash.xlsx` | B | 9700 名片 LLM 结果，10428 行（含 KP 标注） |
| `广交会139届_AB链路对比报告.xlsx` | 对比 | `compare_ab.sh` 生成 |

```
PDF (input/) → 火山 OCR → output/ocr_run_*
                ├─ A：规则 → 汇总.xlsx
                └─ B：DeepSeek → 汇总_dsv4flash.xlsx
```

---

## 全量结果（漏斗）

| 阶段 | 数量 |
|------|------|
| PDF | 12 |
| OCR 页 | 1633（0 失败） |
| 有文字名片 | 9700 |
| 链路 A 手机号行 | 9470 |
| 链路 B 输出行 | 10428 |

---

## 常用命令

```bash
source .venv/bin/activate
cp .env.example .env   # VOLC_* + DEEPSEEK_API_KEY
```

```bash
./scripts/run_ocr.sh              # 链路 A，断点续传
cd src && python run_pipeline.py --data-dir ../data --status   # 查看进度
cd src && python merge_phone_exports.py   # 重新合并 A 汇总
./scripts/run_deepseek_kp.sh --export-only   # 仅从缓存导出 B xlsx
./scripts/compare_ab.sh           # 生成 A/B 对比报告
```

默认 `--data-dir` 指向 `data/`；PDF 从 `data/input/` 读取，OCR 写入 `data/output/`。

---

## 断点续传

- **OCR**：`checkpoint.OCRCheckpoint`，页级 `checkpoint.json` + `ocr_results.jsonl`
- **DeepSeek**：名片级 `completed_keys`，缓存目录 `data/cache/deepseek/`

模式：`稳定 ID` + `原子写 checkpoint` + `增量结果文件` + `--resume`。新增长耗时任务可复制该模式（见 `checkpoint.py` 注释）。

---

## A/B 对比报告解读

文件：`data/deliverables/广交会139届_AB链路对比报告.xlsx`

### Sheet「概览」

| 指标 | 含义 |
|------|------|
| `rule_phone_rows` / `deepseek_phone_rows` | 两链路输出行数（名片×手机号展开） |
| `rule_unique_phones` / `deepseek_unique_phones` | 去重后的手机号个数 |
| `phones_in_both` | 两链路都识别的号（**8747**） |
| `phones_only_in_rule` | 仅 A 有（**411**），多为规则更宽松的正则命中 |
| `phones_only_in_deepseek` | 仅 B 有（**283**），多为 LLM 从噪声 OCR 中抽出 |
| `cards_phone_set_equal` | 同一名片两链路手机号集合完全一致（**8063** 张） |
| `cards_phone_set_diff` | 同名片但号码集合不同（**303** 张） |
| `cards_with_name_std_deepseek` vs `cards_with_name_rule` | B 姓名字段填充更多（8856 vs 8359） |

### Sheet「结论与选型」

- **要稳、便宜、可复现的手机号** → 链路 A
- **要标准化字段 + KP 分层** → 链路 B（约 5061 张名片标为 KP）
- **推荐组合**：A 作手机号基线 + B 的 `is_key_person` / `*_std`；411+283 差异号抽样核对

### Sheet「名片级差异样例」

最多 500 条典型案例：`仅链路A有号` / `仅链路B有号` / `手机号集合不一致`，含 `card_key` 可回溯到 `output/` 中对应 OCR 目录。

### Sheet「手机号单边样例」

仅 A 或仅 B 出现的手机号列表，便于抽检。

---

## 安全

勿提交 `.env`、PDF（`data/input/`）及 `data/output/`、`data/cache/`。密钥请轮换。
