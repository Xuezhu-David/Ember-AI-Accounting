# Ember AI — 原生财务记账系统

> 基于 AI Agent 的 SAP 会计凭证自动生成与管理平台
>
> 本项目 Fork 自 [github.com/duzhuo/ember-ai-accounting](https://github.com/duzhuo/ember-ai-accounting)，在原有基础上扩展了 SAP CSV 导出、批量上传、移动端、审批流等功能。

---

## 一、Agent 的用处

### 1. 自然语言 → 会计凭证

用户只需用中文描述一笔交易，Agent 就能自动：

- 识别业务类型（销售收入、费用报销、资产采购、工资薪酬、借款还款……）
- 匹配对应的**凭证规则**（借贷科目、税码、利润中心等）
- 计算含税/不含税金额、增值税
- 生成标准 SAP 格式的会计凭证草稿（BUKRS/BLART/HKONT/WRBTR 等字段）

示例输入：
```
销售软件产品给华为公司，不含税金额 100,000 元，税率 13%
```

Agent 输出包含三行分录的凭证：应收账款（借）/ 主营业务收入（贷）/ 应交税费（贷）。

### 2. 图片/PDF/Excel → 凭证（OCR + 解析）

上传发票图片、PDF 或 Excel，OCR Agent 自动提取关键字段，再由 Voucher Agent 生成凭证。支持批量拖入多文件。

### 3. 凭证全生命周期管理

- 草稿 / 待审批 / 已过账 / 已冲销 全状态管理
- 支持凭证搜索、过滤、批量过账
- 操作全程写入审计日志

### 4. SAP CSV 导出

一键导出符合 SAP 批量导入格式的 CSV 文件，字段对应：

| CSV 列 | SAP 字段 |
|--------|----------|
| BUKRS | 公司代码 |
| BLART | 凭证类型 |
| BLDAT | 凭证日期 |
| BUDAT | 过账日期 |
| HKONT | 科目代码 |
| WRBTR | 金额 |
| SHKZG | 借/贷标志 |
| KUNNR | 客户编码 |
| MWSKZ | 税码 |
| PRCTR | 利润中心 |
| KOSTL | 成本中心 |
| SGTXT | 摘要 |

### 5. 审批流（预制）

凭证支持"提交审批 → 指定审批人 → 审批通过/驳回"流程：

```
draft → pending_approval → posted（通过）
                        → draft（驳回，可修改后重提）
```

API 端点已完整实现（`routes/approval.py`），可按需接入企业通知（邮件/IM/企业微信）。

---

## 二、架构概览

```
┌─────────────────────────────────────────────────────┐
│                     前端                            │
│  PC：index.html + script.js + style.css             │
│  移动：mobile.html + mobile.js + mobile.css         │
│  协议：A2UI v0.9（声明式 UI 组件，JSON 驱动）        │
└──────────────────┬──────────────────────────────────┘
                   │ HTTP / SSE
┌──────────────────▼──────────────────────────────────┐
│              FastAPI 后端  server.py                 │
│  routes/: chat  upload  confirm  vouchers  rules     │
│           auth  attachments  export  approval  audit │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│           AgentScope 2.0 多 Agent 层                 │
│  IntentAgent   — 意图识别 + 结构化抽取               │
│  VoucherAgent  — 凭证规则匹配 + 分录生成             │
│  OcrAgent      — 图片/PDF 文字提取与解析             │
└──────────────────┬──────────────────────────────────┘
                   │ LLM API
┌──────────────────▼──────────────────────────────────┐
│         大模型（可替换，见下方说明）                  │
│  默认：Claude claude-sonnet-4-5（Anthropic）         │
└─────────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│      SQLite（aiosqlite + WAL）  database.py          │
│  voucher_records / approval_records / users          │
│  chat_sessions / audit_logs / attachments            │
└─────────────────────────────────────────────────────┘
```

---

## 三、快速开始

### 1. 环境准备

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置

复制 `.env.example` → `.env`，填入：

```env
# Anthropic Claude（默认）
ANTHROPIC_AUTH_TOKEN=sk-ant-...
ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-4-5

# 或 DeepSeek（见下方替换说明）
# DEEPSEEK_API_KEY=sk-...
```

### 3. 启动

```bash
python server.py
# 访问 http://localhost:8000
# 默认账号：admin / admin123
```

启动时自动完成：数据库初始化、默认管理员创建、凭证规则种子数据写入、AgentScope Agent 初始化。

---

## 四、将模型替换为 DeepSeek（或其他兼容 OpenAI 的模型）

所有 Agent 的模型创建统一在 **`agents/model_factory.py`**，只需修改这一个文件。

DeepSeek API 兼容 OpenAI 格式：

### 方案 A：换 Base URL（最简单）

```python
# agents/model_factory.py

from agentscope.credential import OpenAIChatCredential
from agentscope.model import OpenAIChatModel

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

def create_chat_model(vision: bool = False) -> OpenAIChatModel:
    return OpenAIChatModel(
        credential=OpenAIChatCredential(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1",
        ),
        model="deepseek-chat",
        stream=True,
        parameters=OpenAIChatModel.Parameters(temperature=0.1),
    )
```

`.env` 中加：

```env
DEEPSEEK_API_KEY=sk-...
```

### 方案 B：动态切换

在 `model_factory.py` 中读取 `LLM_PROVIDER` 环境变量（`anthropic` / `deepseek` / `openai`），根据值选择对应的模型类，无需改代码即可运行时切换。

### 注意事项

| 项目 | 说明 |
|------|------|
| 视觉能力 | DeepSeek `deepseek-chat` 不支持 vision；OCR 功能可单独保留 Claude 视觉模型（混用） |
| 提示词兼容性 | `IntentAgent` / `VoucherAgent` 的 Prompt 基于 `agentscope.message.Msg` 接口，与底层模型解耦，无需修改 |
| 本地部署 | Ollama / vLLM 同样仅需修改 `base_url` 和 `model` 参数 |

---

## 五、扩展性

| 扩展方向 | 实现位置 | 说明 |
|---------|---------|------|
| 新增业务凭证类型 | `data/rules/*.xlsx` + DB `voucher_rules` 表 | 在规则表中增加新规则，Agent 自动匹配，无需改代码 |
| 新增 Agent | `agents/` | 继承 `agentscope.agent.Agent`，注册到 `intent_agent` 的路由表 |
| 接入企业 SSO | `routes/auth.py` | 替换 session 登录为 SAML/OAuth2 |
| 邮件/IM 审批通知 | `routes/approval.py` | `submit_for_approval` 后调用 SMTP/Exchange/企业微信 API |
| 多公司代码 | `database.py` + `agents/voucher_agent.py` | 在凭证上下文中加入 `company_code` 参数 |
| 导出到 SAP RFC | `helpers/csv_export.py` | 用 PyRFC 替换 CSV 写文件 |
| 多语言 UI | `index.html` / `mobile.html` | 将硬编码中文字符串改为 i18n key |

---

## 六、API 接口

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录（含频率限制） |
| POST | `/api/auth/logout` | 登出 |
| GET | `/api/auth/me` | 当前用户信息 |
| PUT | `/api/auth/password` | 修改密码 |

### 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 发送消息（SSE 流式响应） |
| POST | `/api/upload` | 上传文件（SSE 流式响应） |

### 凭证

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/vouchers` | 凭证列表（支持 keyword/status/date 筛选） |
| GET | `/api/vouchers/{id}` | 凭证详情 |
| PUT | `/api/vouchers/{id}` | 更新草稿凭证 |
| POST | `/api/vouchers/{id}/reverse` | 冲销已过账凭证 |
| POST | `/api/confirm` | 单条过账 |
| POST | `/api/confirm/batch` | 批量过账 |

### 审批流

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/vouchers/{id}/submit` | 提交审批（`approver_id` 或 `no_approval=true`） |
| POST | `/api/vouchers/{id}/approve` | 审批通过（凭证过账） |
| POST | `/api/vouchers/{id}/reject` | 驳回（附原因，凭证回到草稿） |
| GET | `/api/approvals/pending` | 待我审批的凭证列表 |
| GET | `/api/users/approvers` | 可选审批人列表 |

### 导出

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/export/csv?ids=id1,id2,...` | 导出指定凭证为 SAP CSV |
| GET | `/api/export/csv/all` | 导出全部已过账凭证 CSV |

### 规则

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/rules` | 规则列表 |
| POST | `/api/rules` | 新增规则（管理员） |
| PUT | `/api/rules/{code}` | 修改规则（管理员） |
| DELETE | `/api/rules/{code}` | 删除规则（管理员） |

---

## 七、目录结构

```
ember-ai-accounting/
├── server.py              # FastAPI 入口
├── database.py            # SQLite 数据层（aiosqlite）
├── agents/
│   ├── model_factory.py   # ← 替换模型的唯一入口
│   ├── intent_agent.py    # 意图分类 Agent
│   ├── voucher_agent.py   # 凭证生成 Agent
│   └── ocr_agent.py       # OCR Agent
├── routes/
│   ├── chat.py            # SSE 聊天流
│   ├── upload.py          # 文件上传 + OCR
│   ├── confirm.py         # 过账
│   ├── approval.py        # 审批流 API（新增）
│   ├── export.py          # SAP CSV 导出（新增）
│   └── ...
├── helpers/
│   ├── a2ui.py            # A2UI v0.9 声明式 UI 构建
│   ├── csv_export.py      # SAP CSV 列映射
│   └── auth.py            # 会话鉴权
├── prompts/               # 系统提示词（.md，可直接编辑）
├── data/
│   ├── rules/             # 凭证规则 Excel
│   └── output/            # 导出 CSV
├── index.html             # PC 端
├── mobile.html            # 移动端（新增）
└── style.css / mobile.css
```

---

## 八、技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.11+, FastAPI, Uvicorn |
| Agent 框架 | AgentScope 2.0 |
| 数据库 | SQLite (aiosqlite), WAL 模式 |
| 密码安全 | bcrypt |
| Excel 处理 | openpyxl |
| PDF 识别 | PyMuPDF |
| PDF 导出 | WeasyPrint + Jinja2 |
| 前端 | 原生 HTML/CSS/JS, Phosphor Icons |
| LLM（默认） | Claude claude-sonnet-4-5（Anthropic），可替换为 DeepSeek/任意 OpenAI 兼容模型 |

---

## 九、致谢

- **原始项目**：[github.com/duzhuo/ember-ai-accounting](https://github.com/duzhuo/ember-ai-accounting) — 感谢原作者提供的基础架构与 Agent 设计
- **多 Agent 框架**：[AgentScope 2.0](https://github.com/modelscope/agentscope)
- **LLM**：Anthropic Claude（默认）/ DeepSeek / 其他 OpenAI 兼容模型
