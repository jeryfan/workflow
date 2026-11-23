# Workflow

基于固定 Dify 前端快照的独立工作流项目。默认前端为 Vite 8 + React 19 + TypeScript，直接复用原工作流组件、样式、生成式 Console 客户端和 SSE 处理；`web-legacy/` 中的旧前端继续保留。后端为 FastAPI + Graphon，按路由、请求模型、业务服务、ORM 模型和依赖分层。

## Docker Compose 部署

`docker/docker-compose.yaml` 是唯一部署入口；API 与旧版前端的 Dockerfile 分别位于 `api/` 和 `web-legacy/`。新版 Vite 只负责构建静态文件，不需要独立运行容器。共享的 Nginx、SSRF 与环境模板位于 `docker/`。

```bash
make dev
```

`make dev` 会在缺少 `docker/.env` 时从模板生成随机本地密钥，然后构建新版前端、同步静态文件到 `docker/www/workflow` 并启动服务。已有环境文件不会被覆盖；生产部署应使用外部密钥管理替换这些值。对外部署时将 `PUBLIC_ORIGIN` 设置为实际 HTTPS 地址。

Nginx 以只读方式挂载 `docker/www`，并将 `workflow` 子目录作为新版文档根目录。打开 `http://localhost`。首次使用先初始化 owner，再通过邮箱密码登录。默认只通过宿主机 80 端口公开 Nginx，PostgreSQL、Redis、RustFS、API、Sandbox 和 SSRF Proxy 不暴露宿主机端口。

默认服务：

- `nginx`：托管 `docker/www/workflow` 中的 Vite 静态产物，代理 Console、公开运行和 Webhook 接口。
- `api`：FastAPI、鉴权、业务校验、兼容响应与数据库迁移。
- `worker`：独立 Celery Worker，执行工作流。
- `beat`：独立 Celery Beat，调度定时触发器。
- `postgres`、`redis`、`rustfs`：业务数据、任务队列和 S3 兼容对象存储。
- `sandbox`、`ssrf-proxy`：代码执行和受控出站请求。HTTP 节点及远程文件下载统一经 SSRF Proxy 防护，不应绕过或删除。

生产环境使用 PostgreSQL、Celery、Redis 和 S3 兼容对象存储。账号、草稿、发布版本、运行记录、触发器和文件分别保存在 `postgres-data`、`redis-data`、`object-data` 命名卷中。普通重启及不含 `-v` 的 `docker compose down` 保留数据。

数据库结构统一由 Alembic 管理，API 启动时升级已有修订；不提供自定义 SQLite 导入或第二套迁移机制。

### LLM 模型配置

LLM 节点使用 Pydantic AI，模型和供应商由后端通过 `docker/.env` 的 `LLM_MODELS` JSON 数组配置。`GET /console/api/models` 返回扁平模型目录，每项包含稳定 `id`、显示名称、运行时模型名、上下文长度、最大输出、能力和默认标记；前端只按 `id` 选择和恢复模型，不处理供应商插件目录。`id` 必须唯一，`provider` 使用 Pydantic AI 的供应商名称；`api_key`、`base_url`、`timeout`、`max_retries`、`context_length`、`max_output_tokens`、`completion_params` 和 `stop_sequences` 可选，`features` 可声明 `vision`、`structured-output`，`is_default` 设置默认模型。自定义 OpenAI 兼容服务使用 `provider: "openai-compatible"` 并配置 `base_url`。

```dotenv
LLM_MODELS=[{"id":"deepseek-flash","provider":"deepseek","name":"deepseek-flash","label":"DeepSeek-V4.1-Flash","api_key":"replace-with-a-managed-secret","features":["structured-output"],"context_length":1048576,"max_output_tokens":393216,"is_default":true}]
```

凭据只用于后端调用，不会进入模型目录响应或工作流草稿。修改后重建并重启 API、Worker 和 Beat；未配置模型时，LLM 节点仍可编辑提示词，但运行会明确报告模型未配置。

## 功能范围

支持工作流列表、搜索、创建、元数据编辑、复制、删除、DSL 导入导出、草稿保存、环境变量、调试运行、SSE、运行历史、发布与版本恢复。发布后可通过访问入口启停站点、复制地址、重置令牌，并使用公开运行页。

工作流可发布为内部工具，并通过其他工作流的 Tool 节点调用已发布版本；包含必填参数校验、循环检测和嵌套深度限制。支持定时触发器和 Webhook 触发器，调试地址运行草稿，正式地址运行已发布且启用的触发器。

当前可执行节点：`start`、`llm`、`template-transform`、`code`、`if-else`、`variable-aggregator`、`list-operator`、`http-request`、`iteration`、`loop`、`document-extractor`、`human-input`、`end`、`tool`、`trigger-schedule` 和 `trigger-webhook`；容器内部另有 `iteration-start`、`loop-start`、`loop-end` 和 `assigner`。

开始节点支持文本及文件/文件列表。LLM 节点支持提示词模板、流式文本、结构化输出、结束原因和 token 用量。文档提取节点支持文本和常见文档格式解析。人工输入节点支持表单、审批分支、暂停快照、一次性提交、超时和恢复。上传、预览、运行结果下载和历史下载均有接口与行为测试；单文件上限 20 MB，危险可执行文件扩展名会被拒绝，下载使用限时 HMAC 签名地址。

已移除工作区及工作流独立访问授权、无关工作区菜单、Snippets 入口、节点作者署名、帮助文档入口和问号菜单。仅保留 `en-US`、`zh-Hans`。知识库、外部插件工具、Agent 和插件触发器不在当前可执行范围内；未支持节点不会出现在选择器中。Human Input V2 的工作区联系人、IM 渠道和对应接口不属于支持范围，不以 `skip` 保留依赖这些能力的失败测试；如需外部审批通知，应基于本项目账户或邮箱模型单独设计。

管理员可创建成员，密码通过交互提示或标准输入提供：

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yaml exec api workflow create-member \
  --email editor@example.com --name "Workflow Editor" --role editor
```

多人实时协同保持关闭，不以轮询替代协同协议。

## 目录结构

- `api/src/api/routers/`：HTTP 路由；`schemas/`：请求与响应边界；`services/`：业务与运行服务。
- `api/src/api/models/`：SQLAlchemy ORM 模型与会话；`api/alembic/`：Alembic 配置及版本修订。
- `api/src/api/dependencies/`、`middlewares/`、`libs/`：请求依赖、中间件和通用安全组件。
- `api/src/api/configs/`、`extensions/`：分组配置与数据库外扩展初始化。
- `api/src/api/core/graphon/`：固定版本的 Graphon vendor 源码；`core/nodes/`：本项目自定义触发器节点。
- `web/`：默认 Vite + React TS 入口、路由和浏览器兼容层；业务组件直接复用旧前端，不复制实现。
- `web-legacy/`：保留的旧前端及组件、样式、接口和测试来源。
- `packages/`：UI 组件、生成式接口契约与共享工作区包。
- `contracts/`：节点、前端同步和测试夹具契约。
- `tools/`：同步、生成、迁移检查和端到端验证工具，仅用于开发维护，不进入生产镜像。
- `docker/`：Compose、共享 Nginx/SSRF 配置与环境模板；各服务的 Dockerfile 和入口脚本归各自目录。
- `.data/`：本地开发数据和验收输出，已被 Git/Docker 忽略；生产使用命名卷与对象存储。

## 本地开发

要求 Python 3.13、Node `^24.22.3` 和 pnpm 12.18.0。完整开发环境推荐直接使用 `make dev`。如果需要在容器外单独启动 API，应准备可访问的 PostgreSQL 与 Redis，并按实际地址配置 `DB_HOST`、`DB_PORT`、`REDIS_HOST` 和 `REDIS_PORT`。

```bash
uv sync --project api --group dev
corepack pnpm install --frozen-lockfile
DB_PASSWORD=local-only \
SECRET_KEY=local-development-secret-key-at-least-32-bytes \
FILE_SIGNING_KEY=local-development-signing-key-at-least-32-bytes \
  uv run --project api --no-sync alembic -c api/alembic.ini upgrade head
DB_PASSWORD=local-only \
SECRET_KEY=local-development-secret-key-at-least-32-bytes \
FILE_SIGNING_KEY=local-development-signing-key-at-least-32-bytes \
  uv run --project api --no-sync uvicorn api.app:app --host 127.0.0.1 --port 8000
```

另开终端启动 Vite，默认端口为 3100，并把接口代理到 8000；可通过 `VITE_API_PROXY_TARGET` 覆盖代理目标：

```bash
corepack pnpm -C web dev
```

旧前端仍可用于样式与行为对照，启动时显式提供接口地址，避免使用上游 Dify 的 5001 端口默认值：

```bash
NEXT_PUBLIC_API_PREFIX=http://127.0.0.1:8000/console/api \
NEXT_PUBLIC_PUBLIC_API_PREFIX=http://127.0.0.1:8000/api \
NEXT_PUBLIC_SOCKET_URL=ws://127.0.0.1:8000 \
CONSOLE_API_URL=http://127.0.0.1:8000 \
SERVER_CONSOLE_API_URL=http://127.0.0.1:8000 \
  corepack pnpm -C web-legacy dev
```

也可通过可选容器同时对比：新版为 `http://localhost`，旧版由同一 Nginx 通过 `http://legacy.localhost` 反向代理。compare 是同一个 Compose 文件中的 profile，宿主机仍只开放 80 端口：

```bash
make compare
```

## 验证

运行完整支持范围回归：

```bash
make test
```

该入口安装锁定依赖，执行后端规范、项目工具和契约检查，自动启动并清理独立的 PostgreSQL、Redis、RustFS 容器，在真实迁移后的数据库上运行全部后端测试，再执行前端类型、语言资源、单元测试、生产构建和逐文件隔离的浏览器组件测试。浏览器运行环境缺失时会自动安装到 `.tools/`；测试日志写入 `.data/test/`。不使用跳过列表隐藏不支持功能的失败。

真实 Compose 接口验收：

```bash
WORKFLOW_API_URL=http://127.0.0.1 \
WORKFLOW_ADMIN_EMAIL=admin@example.com WORKFLOW_ADMIN_PASSWORD='your-password' \
  python3 tools/verify_live_api.py

# 使用 LLM_MODELS 中的默认配置实际调用供应商，并清理临时工作流
WORKFLOW_API_URL=http://127.0.0.1 \
WORKFLOW_ADMIN_EMAIL=admin@example.com WORKFLOW_ADMIN_PASSWORD='your-password' \
  make test-llm

WEB_BASE_URL=http://127.0.0.1 node tools/verify_vite_ui.mjs

# 经同一 80 端口对比默认入口新版与 legacy.localhost 旧版
node tools/compare_frontends.mjs
```

真实模型测试会产生供应商调用费用，因此不包含在普通 `make test` 中。模拟供应商测试只证明协议与工作流链路，不应作为真实模型可用性的结论。

浏览器脚本默认使用 Playwright Chromium；可通过 `CHROME_PATH` 指定现有 Chrome。基础脚本覆盖登录、列表、编辑器和二次加载；对比脚本还覆盖认证跳转、列表筛选与搜索、创建和 YAML 导入弹窗、账户与卡片菜单、编辑器节点及面板、节点目录标签、发布与工具配置抽屉、访问入口弹窗和已发布公开页。每个前端使用独立的临时工作流，并在结束后删除。截图、页面结构、请求与错误记录写入 `.data/acceptance/frontend-matrix/`。危险操作只打开并取消，不删除或发布现有工作流。

## 上游同步

Dify 前端固定为提交 `bdbe2d012035bbb12f3c0bee87867565cab77360`。`web-legacy/upstream-manifest.json` 记录哈希，功能删减补丁集中在 `contracts/frontend-patches/`，本地文件与删除项登记在 `contracts/frontend.json`。

```bash
python3 tools/frontend_parity.py sync --source /path/to/dify --project-root .
python3 tools/generate_node_registry.py
```

Graphon 来源固定在 `contracts/sources.json`。正常运行和 Docker 构建不需要上游 checkout。

## 许可证

Dify 使用带附加条件的 Apache 2.0 许可证；保留 `web-legacy/LICENSE`、`web-legacy/NOTICE` 和必要版权信息。Graphon 使用 Apache 2.0，许可证随 vendor 源码保留。
