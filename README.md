# Volc Budget Guard

## 预算周期

每个项目可同时配置 `monthly`、`quarterly`、`yearly` 和 `lifetime`。系统会从已同步到本地数据库的账单中分别累计四种周期，并采用最严格的状态执行限流。

```yaml
projects:
  project-a:
    project_start_date: "2026-01-01"
    budgets:
      monthly:
        amount: "10000.00"
        warning_ratio: "0.80"
        throttle_ratio: "0.95"
        block_ratio: "1.00"
      quarterly:
        amount: "28000.00"
        warning_ratio: "0.80"
        throttle_ratio: "0.95"
        block_ratio: "1.00"
      yearly:
        amount: "100000.00"
        warning_ratio: "0.80"
        throttle_ratio: "0.95"
        block_ratio: "1.00"
      lifetime:
        amount: "150000.00"
        warning_ratio: "0.80"
        throttle_ratio: "0.95"
        block_ratio: "1.00"
    throttle_rps: 2
    enabled: true
```

首次启用季度、年度或生命周期预算时，请依次执行 `budget-guard poll --billing-cycle YYYY-MM` 回填所需历史月份。历史账期只同步数据，不执行限流；回填后再轮询当前账期进行预算判断。

按火山引擎项目分账账单轮询，并在项目达到预算阈值时调用你方网关进行预警、限流或封禁。

## 安全设计

- 默认 `DRY_RUN=true`，不会实际封禁。
- 优先限制你方业务入口，不直接删除云资源。
- 阈值状态：`normal -> warning -> throttled -> blocked`。
- 每个账期、项目、状态只执行一次动作，避免重复调用。
- 明细使用 upsert，允许账单延迟更新后重新计算。
- 建议增加“预计消费”指标；分账账单有延迟，单靠账单无法做到实时止损。

## 启动

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
budget-guard init
budget-guard poll --billing-cycle 2026-07
uvicorn budget_guard.api.main:app --reload
```

访问：
- `GET /health`
- `POST /poll?billing_cycle=2026-07`
- `GET /events`

## 接入真实火山账单

1. 在费用中心 OpenAPI 调试选择 `ListSplitBillDetail`。
2. 生成 Python SDK 示例。
3. 将分页调用粘贴到 `src/budget_guard/adapters/billing.py` 的 `_request_page()`。
4. 设置 `BILLING_PROVIDER=volc` 和 AK/SK。

请求建议：`BillPeriod`, `Offset`, `Limit<=300`, `NeedRecordNum=1`。账单 API 单账号限流为 5 QPS，轮询应串行并控制频率。

## 接入你方限流系统

默认调用：

```http
PUT /internal/projects/{project_id}/access
Authorization: Bearer <token>
Content-Type: application/json

{"state":"throttled","rps":2}
```

封禁请求为：

```json
{"state":"blocked","rps":0}
```

将 URL 配置在 `LIMITER_WEBHOOK_URL`。网关应做到幂等，并保留管理员手工解封能力。

## 生产增强

1. SQLite 换 PostgreSQL。
2. 增加企业微信/飞书告警。
3. 加入实时用量计数：请求数、Token、GPU 时长或带宽等。
4. 账单金额用于对账，实时用量用于提前止损。
5. 限流动作至少经过连续两次超阈值或设置缓冲金额，避免账单修正造成抖动。
6. 对核心项目设置白名单或只限流不封禁。
