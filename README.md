# Workflow

基于固定 Dify 前端快照的独立工作流项目。默认前端为 Vite 8 + React 19 + TypeScript，直接复用原工作流组件、样式、生成式 Console 客户端和 SSE 处理；`web-legacy/` 中的旧前端继续保留。后端为 FastAPI + Graphon，按路由、请求模型、业务服务、数据访问和依赖分层。

## Docker Compose 部署

`docker/compose.yaml` 是默认部署入口；API 与旧版前端的 Dockerfile 分别位于 `api/` 和 `web-legacy/`。新版 Vite 只负责构建静态文件，不需要独立运行容器。共享的 Nginx、SSRF 与环境模板位于 `docker/`。

```bash
cp docker/.env.example docker/.env
```

编辑 `docker/.env`，设置 `POSTGRES_PASSWORD`、`FILE_SIGNING_KEY`、`S3_ACCESS_KEY`、`S3_SECRET_KEY` 和 `SANDBOX_API_KEY`。文件签名密钥至少 32 字节。对外部署时将 `PUBLIC_ORIGIN` 设置为实际 HTTPS 地址。

```bash
make dev
```

`build-web.sh` 会构建新版前端并将静态文件同步到 `docker/www/workflow`，不会覆盖 `www` 下的其他资源。Nginx 以只读方式挂载 `docker/www`，并将 `workflow` 子目录作为新版文档根目录。打开 `http://localhost`。首次使用先初始化 owner，再通过邮箱密码登录。默认只通过宿主机 80 端口公开 Nginx，PostgreSQL、Redis、RustFS、API、Sandbox 和 SSRF Proxy 不暴露宿主机端口。

默认服务：

- `nginx`：托管 `docker/www/workflow` 中的 Vite 静态产物，代理 Console、公开运行和 Webhook 接口。
- `api`：FastAPI、鉴权、业务校验、兼容响应与数据库迁移。
- `worker`：独立 Celery Worker，执行工作流。
- `beat`：独立 Celery Beat，调度定时触发器。
- `postgres`、`redis`、`rustfs`：业务数据、任务队列和 S3 兼容对象存储。
- `sandbox`、`ssrf_proxy`：代码执行和受控出站请求。

`APP_ENV=production` 要求 PostgreSQL、Celery、S3 和共享文件签名密钥。账号、草稿、发布版本、运行记录、触发器和文件分别保存在 `postgres-data`、`redis-data`、`object-data` 命名卷中。普通重启及不含 `-v` 的 `docker compose down` 保留数据。

### 从旧 SQLite 部署迁移

旧 `workflow-data` 卷以只读方式挂载到 API 的 `/legacy`。升级到一个新的、空的 PostgreSQL 数据库时，在 `docker/.env` 中设置一次：

```bash
MIGRATE_FROM_SQLITE=/legacy/workflow.db
```

然后执行 `docker compose --env-file docker/.env -f docker/compose.yaml up --build`。迁移器会复制并升级 SQLite 快照，将账号、工作流、版本、运行记录、触发器和 `/legacy/uploads` 中的文件导入 PostgreSQL 与 S3。它拒绝导入非空目标数据库，避免覆盖现有数据。成功后移除该变量。Worker 和 Beat 不执行此导入。

## 功能范围

支持工作流列表、搜索、创建、元数据编辑、复制、删除、DSL 导入导出、草稿保存、环境变量、调试运行、SSE、运行历史、发布与版本恢复。发布后可通过访问入口启停站点、复制地址、重置令牌，并使用公开运行页。

工作流可发布为内部工具，并通过其他工作流的 Tool 节点调用已发布版本；包含必填参数校验、循环检测和嵌套深度限制。支持定时触发器和 Webhook 触发器，调试地址运行草稿，正式地址运行已发布且启用的触发器。

当前可执行节点：`start`、`template-transform`、`code`、`if-else`、`variable-aggregator`、`list-operator`、`http-request`、`iteration`、`loop`、`end`、`tool`、`trigger-schedule` 和 `trigger-webhook`；容器内部另有 `iteration-start`、`loop-start`、`loop-end` 和 `assigner`。

开始节点支持文本及文件/文件列表。上传、预览、运行结果下载和历史下载均有接口与行为测试；单文件上限 20 MB，危险可执行文件扩展名会被拒绝，下载使用限时 HMAC 签名地址。

已移除工作区及工作流独立访问授权、无关工作区菜单、Snippets 入口、节点作者署名、帮助文档入口和问号菜单。仅保留 `en-US`、`zh-Hans`。知识库、外部插件工具、Agent、插件触发器、模型节点和 Human Input 不在当前可执行范围内；未支持节点不会出现在选择器中。

管理员可创建成员：

```bash
docker compose --env-file docker/.env -f docker/compose.yaml exec api python -m api.cli create-member \
  --email editor@example.com --name "Workflow Editor" --role editor
```

多人实时协同保持关闭，不以轮询替代协同协议。

## 目录结构

- `api/src/api/routes/`：HTTP 路由；`schemas/`：请求与响应边界；`services/`：业务与运行服务。
- `api/src/api/models/`：领域实体；`db/`：Store、Alembic 与迁移；`dependencies/`、`security/`：认证依赖与 Cookie 安全。
- `api/src/graphon/`：固定版本的执行引擎 vendor 源码。
- `web/`：默认 Vite + React TS 入口、路由和浏览器兼容层；业务组件不复制。
- `web-legacy/`：保留的旧前端及组件、样式、接口和测试来源。
- `packages/`：UI 组件、生成式接口契约与共享工作区包。
- `contracts/`：节点、权限、前端同步和测试夹具契约。
- `tools/`：同步、生成、迁移检查和端到端验证工具，仅用于开发维护，不进入生产镜像。
- `docker/`：Compose、共享 Nginx/SSRF 配置与环境模板；各服务的 Dockerfile 和入口脚本归各自目录。
- `.data/`：本地开发数据库、上传文件和验收输出，已被 Git/Docker 忽略；生产使用命名卷与对象存储。

## 本地开发

要求 Python 3.13、Node `^24.22.3` 和 pnpm 12.18.0。

```bash
uv sync --project api --group dev
corepack pnpm install --frozen-lockfile
uv run --project api --no-sync uvicorn api.main:app --host 127.0.0.1 --port 5001
```

另开终端启动 Vite，默认端口为 3100，并把接口代理到 5001：

```bash
corepack pnpm -C web dev
```

旧前端仍可用 `corepack pnpm -C web-legacy dev` 启动，用于样式与行为对照；不再是默认生产构建。也可通过可选容器同时对比：新版为 `http://localhost`，旧版由同一 Nginx 通过 `http://legacy.localhost` 反向代理。宿主机只开放 80 端口：

```bash
make compare
```

## 验证

```bash
uv run --project api --no-sync pytest api/tests
uv run --project api --no-sync ruff check api/src api/tests
uv run --project api --no-sync ruff format --check api/src api/tests
corepack pnpm -C web-legacy type-check
corepack pnpm -C web-legacy exec vp test run --project unit --maxWorkers=2
corepack pnpm -C web type-check
corepack pnpm -C web build
python3 -m unittest discover -s tools/tests -v
python3 tools/frontend_parity.py check --project-root .
python3 tools/generate_node_registry.py --check
python3 tools/check_extraction.py
python3 tools/verify_clean_api.py
```

真实 Compose 验收：

```bash
WORKFLOW_API_URL=http://127.0.0.1 \
WORKFLOW_ADMIN_EMAIL=admin@example.com WORKFLOW_ADMIN_PASSWORD='your-password' \
  python3 tools/verify_live_api.py

WEB_BASE_URL=http://127.0.0.1 node tools/verify_vite_ui.mjs

# 经同一 80 端口对比默认入口新版与 legacy.localhost 旧版
node tools/compare_frontends.mjs
```

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
