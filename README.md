# Volc Budget Guard

## 子公司按项目分账

每个子公司映射一个火山引擎 `Project`。系统读取分账明细的 `Project`、`PayableAmount` 和 `Currency`，按子公司累计 `monthly`、`quarterly`、`yearly` 和 `total` 四种预算，并采用最严格的状态执行限流。

```yaml
subsidiaries:
  project-a:
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
    control:
      stop_endpoints_on_block: true
      disable_iam_access_keys_on_block: false
      iam_user_name: ""
      iam_access_key_ids: []
      block_gateway_on_block: false
    throttle_rps: 2
    throttle_concurrency: 1
    enabled: true
```

配置键直接使用火山 `Project`；`volc_project` 必须与账单返回值完全一致。一个火山项目不能同时分配给多个子公司；币种不匹配会中止轮询，避免错误累加。

首次启用季度、年度或总预算时，请依次执行 `budget-guard poll --billing-cycle YYYY-MM` 回填所需历史月份。历史账期只同步数据，不执行限流；回填后再轮询当前账期进行预算判断。

处理链路：`火山 Project → 子公司 → 周期预算 → throttled/blocked → 设置接入点并发数和 RPM → 恢复原限额`。

## 代码结构

```text
domain/models.py       子公司预算与状态模型
adapters/billing.py    火山分账字段适配
adapters/volc.py       火山 OpenAPI 签名与请求
adapters/limiter.py    Endpoint / IAM 自动管控
services/budgets.py    子公司配置、周期窗口与阈值
services/poller.py     Project 汇总和限流编排
api/main.py            轮询与事件查询接口
```

## 安全设计

- 默认 `DRY_RUN=true`，不会实际封禁。
- 封禁把 Endpoint 的并发数和 RPM 限制为 0，不删除或关闭云资源。
- 阈值状态：`normal -> warning -> throttled -> blocked`。
- `blocked` 状态每轮复查，防止新建 Endpoint 绕过限流。
- 仅记录并恢复本系统实际修改的限额，不覆盖修改前的配置。
- 明细使用 upsert，允许账单延迟更新后重新计算。
- 建议增加“预计消费”指标；分账账单有延迟，单靠账单无法做到实时止损。

## 启动

最简单的启动方式：

```bash
python start.py
```

首次运行会自动从 `.env.example` 创建 `.env`，并生成配置页面访问令牌。可在 `.env` 中通过 `APP_PORT=8000` 修改端口，打开终端显示的 `/admin` 地址即可。
服务会按 `POLL_INTERVAL_MINUTES` 自动同步当月账单；配置页也可以选择月份后立即查询。页面金额是最近一次同步结果，火山账单本身可能延迟。

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
- `POST /api/poll?billing_cycle=2026-07`
- `GET /api/bills?billing_cycle=2026-07`
- `GET /events`
- `GET /admin`（预算与账单网页）

配置网页和账单 API 需要在 `.env` 设置 `CONFIG_API_TOKEN`。启动 API 后访问 `/admin`，输入令牌即可配置预算并查询账单。

## 接入真实火山账单

设置 `BILLING_PROVIDER=volc`、`VOLC_ACCESS_KEY` 和 `VOLC_SECRET_KEY` 后重启服务。系统已内置 `ListSplitBillDetail` 的签名和分页请求。

请求建议：`BillPeriod`, `Offset`, `Limit<=300`, `NeedRecordNum=1`。账单 API 单账号限流为 5 QPS，轮询应串行并控制频率。

## 启用自动封禁

`.env` 使用主账号 AK/SK，并保持下面的配置先做演练：

```env
LIMITER_PROVIDER=volc
DRY_RUN=true
VOLC_REGION=cn-beijing
```

在配置网页或 `config/budgets.yaml` 中设置 `throttle_rps`、`throttle_concurrency`，并开启 `stop_endpoints_on_block`。达到 `throttled` 后，系统会按 `ProjectName` 设置普通和预置推理接入点的 RPM/并发数；达到 `blocked` 后将两者设为 0；预算解除后恢复原限额。字段名 `stop_endpoints_on_block` 为兼容旧配置保留，实际不再停止接入点。

需要同时限制 IAM 子账号时，再开启 `disable_iam_access_keys_on_block`，填写 IAM 用户名和 Access Key ID。这里只保存子账号 AK，不保存子账号 SK。主账号需要具有方舟 Endpoint 管理权限，以及启用 IAM 管控时所需的 Access Key 查询和状态更新权限。

确认演练日志和项目映射无误后，将 `DRY_RUN=false` 并重启服务，才会执行真实限流和恢复。

### 方舟调用网关

普通方舟 API Key 没有公开的禁用 OpenAPI。需要实时止损时，不要把方舟 API Key 交给子公司；由自有网关保存方舟 API Key，并给每个子公司签发独立网关令牌。

```env
LIMITER_PROVIDER=volc
LIMITER_WEBHOOK_URL=https://gateway.example.com/internal/projects/{project_id}/access
LIMITER_WEBHOOK_TOKEN=replace-with-a-strong-token
```

网关接收 `PUT` JSON：

```json
{"state": "blocked", "rps": 0}
```

`state` 为 `normal`、`throttled` 或 `blocked`，项目由 URL 中的 `{project_id}` 指定。开启 `block_gateway_on_block` 后，预算超额会自动封禁该 Project，恢复预算时重新放行；页面也支持手动操作。`DRY_RUN=true` 时只记录预计动作。

## 生产增强

1. SQLite 换 PostgreSQL。
2. 增加企业微信/飞书告警。
3. 加入实时用量计数：请求数、Token、GPU 时长或带宽等。
4. 账单金额用于对账，实时用量用于提前止损。
5. 限流动作至少经过连续两次超阈值或设置缓冲金额，避免账单修正造成抖动。
6. 对核心项目设置白名单或只限流不封禁。
