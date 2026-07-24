# Volc Budget Guard

## 子公司按项目分账

每个子公司映射一个火山引擎 `Project`。系统读取分账明细的 `Project`、`PayableAmount` 和 `Currency`，按子公司累计 `monthly`、`quarterly`、`yearly` 和 `total` 四种预算，并采用最严格的状态执行限流。

```yaml
subsidiaries:
  company-a:
    company_name: 子公司A
    volc_project: project-a
    currency: CNY
    project_start_date: "2026-01-01"
    warning_ratio: "0.80"
    throttle_ratio: "0.95"
    block_ratio: "1.00"
    budgets:
      monthly:
        amount: "10000.00"
      quarterly:
        amount: "28000.00"
      yearly:
        amount: "100000.00"
      total:
        amount: "150000.00"
    throttle_rps: 2
    enabled: true
```

`company-a` 是内部子公司标识，`volc_project` 必须与账单实际返回的 `Project` 完全一致。一个火山项目不能同时分配给多个子公司；币种不匹配会中止轮询，避免错误累加。

首次启用季度、年度或总预算时，请依次执行 `budget-guard poll --billing-cycle YYYY-MM` 回填所需历史月份。历史账期只同步数据，不执行限流；回填后再轮询当前账期进行预算判断。

处理链路：`火山 Project → 子公司 → 周期预算 → warning / throttled / blocked → 业务网关`。

## 代码结构

```text
domain/models.py       子公司预算与状态模型
adapters/billing.py    火山分账字段适配
services/budgets.py    子公司配置、周期窗口与阈值
services/poller.py     Project 汇总和限流编排
api/main.py            轮询与事件查询接口
```

## 安全设计

- 默认 `DRY_RUN=true`，不会实际封禁。
- 优先限制你方业务入口，不直接删除云资源。
- 阈值状态：`normal -> warning -> throttled -> blocked`。
- 每个账期、项目、状态只执行一次动作，避免重复调用。
- 明细使用 upsert，允许账单延迟更新后重新计算。
- 建议增加“预计消费”指标；分账账单有延迟，单靠账单无法做到实时止损。

## 启动

最简单的启动方式：

```bash
python start.py
```

首次运行会自动从 `.env.example` 创建 `.env`，并生成配置页面访问令牌。可在 `.env` 中通过 `APP_PORT=8000` 修改端口，打开终端显示的 `/admin` 地址即可。

手动启动方式：

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
- `GET /admin`（预算配置网页）

配置网页需要在 `.env` 设置 `CONFIG_API_TOKEN`。启动 API 后访问 `/admin`，输入令牌即可新增、修改或删除子公司预算；保存时会先执行与轮询相同的配置校验，再原子替换 `budgets.yaml`。

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
