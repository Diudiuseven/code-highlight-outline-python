# code-highlight-outline-python-gen

面向源码的高亮 / 大纲 / 格式化服务。调用方提交源码文本与目标语言，服务返回
语义高亮区间、符号大纲与规范化后的文本，支持增量更新、并发合并与结果缓存。

## 快速开始

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000
uv run pytest tests -q
```

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/analyze?language=<name>` | 请求体为源码原始字节，返回完整分析结果 |
| POST | `/edit` | JSON `{language, old_b64, edit, new_b64}`，增量分析，返回结果与受影响范围 |
| GET | `/languages` | 已注册语言列表 |
| GET | `/config` | 当前生效配置 |

`/analyze` 与 `/edit` 的返回结构：

```json
{
  "language": "python",
  "highlights": [{"range": {"start": {"line": 0, "col": 0}, "end": {"line": 0, "col": 6}}, "category": "keyword"}],
  "outline": [{"name": "f", "kind": "function", "range": ..., "symbol_id": "module/f", "children": []}],
  "formatted": "...",
  "diagnostics": [{"code": "syntax-error", "message": "...", "severity": "error", "position": {"line": 1, "col": 0}}],
  "degraded": false
}
```

`/edit` 额外返回 `affected`：本次编辑在新文本坐标系下、按行对齐的受影响范围。

## 处理语义

### 1. 高亮

- 区间为零基 `(line, col)` 半开区间，按 `(start, end)` 排序，**互不重叠**，
  每个区间可在原文中精确切片还原对应文本。
- 类别为词法类别：`keyword / identifier / string / number / comment /
  operator / punctuation / literal`；派生类别（`class.declaration`、
  `function.declaration`）由 AST 在词法结果上**原位升级**产生，不引入重叠。
- 同一输入在任意并发度与请求顺序下结果逐条一致：分析是纯函数，且相同
  `(language, sha256(content))` 的并发请求被合并为一次计算，所有等待者
  拿到同一个结果对象。

### 2. 符号大纲

- 符号包含名称、种类（`class / function / method / variable / key`）、
  作用范围（range）与嵌套子符号；子符号按 `(起始行, 列, 名称)` 排序，完全确定。
- `symbol_id` 为层级路径（如 `module/Animal/speak`）。
- **重名符号**：同级重名按出现顺序追加 `#2`、`#3` 后缀（如 `module/dup#2`），
  跨运行稳定。
- **匿名符号**：Python 中未命名 lambda 以 `<lambda>` 为名，id 为
  `<lambda>@行:列`；JSON 数组不产生符号（对大纲透明），对象键即符号。

### 3. 格式化

- **幂等**：`format(format(x)) == format(x)`（有测试保证）。
- **不改变语义**：Python 仅做 CRLF/CR→LF、行首 tab 按 8 列制表位展开
  （与 Python 自身缩进规则一致）、去行尾空白、3+ 连续空行压缩为 2、
  保证单个末尾换行；多行字符串覆盖的行一律不触碰（有 AST 等价性测试）。
- JSON 使用确定性美化器：保留键顺序与**重复键**，数字字面量保持原始文本
  （`1e3` 不会被改写为 `1000.0`），字符串按规范转义形式输出。

### 4. 增量处理

- 编辑后仅对**受影响行区域**（编辑区域向外扩展到覆盖所有相交的多行 token）
  重新词法分析，区域外的高亮 token 平移复用；大纲与格式化围绕拼接后的
  高亮结果重算（`rederive`）。
- 仅当新旧文本词法边界均处于"干净状态"且新文本无语法错误时才采用增量结果，
  否则自动回退整份重算——**增量结果与整份重算逐条一致**（含 400+ 条随机
  编辑的模糊测试）。
- 响应中的 `affected` 字段说明受影响范围（新文本坐标，行对齐）。

### 5. 解析容错（降级策略）

请求永不因内容问题整体失败，而是降级并给出可区分的 `diagnostics[].code`
（定位精确到行列）：

| code | 触发 | 降级行为 |
| --- | --- | --- |
| `syntax-error` | 词法/语法错误 | 返回已成功部分的高亮，大纲缺失时为空，格式化退化为仅换行符规范化 |
| `invalid-encoding` | 非法 UTF-8 | 以 U+FFFD 替换后继续分析，诊断给出首个非法字节的行列 |
| `file-too-large` | 超过 `max_file_size` | 不做分析，原文作为 `formatted` 返回，高亮/大纲为空 |
| `timeout` | 超过 `timeout_seconds` | 返回未处理文本，**不写入缓存** |
| `language-not-supported` | 未知/未启用语言 | 请求失败（HTTP 400），**明确报错而非静默降级** |

### 6. 并发合并与缓存

- 相同 `(语言, 内容 SHA-256)` 的并发请求合并为一次计算（single-flight）。
- 缓存按内容寻址（LRU，容量 `cache_capacity`），内容变了键就变，
  **不会返回陈旧内容**；超时结果不入缓存。
- 单个调用方取消通过 `asyncio.shield` 隔离，不会取消共享计算、不污染缓存、
  不留中间状态。

### 7. 语言与配置

`ServiceConfig` 配置项：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `languages` | `None`（全部启用） | 允许的语言集合；集合外语言明确报 `language-not-supported` |
| `cache_capacity` | `256` | LRU 缓存上限（条） |
| `max_file_size` | `2 MiB` | 单文件字节上限，超出按上表降级 |
| `timeout_seconds` | `10.0` | 单次分析超时 |

- 语言插件注册在 `LanguageRegistry` 中相互隔离：新增/替换/注销一个语言
  不影响其他语言的已有结果（有测试保证）。
- 内置语言：`python`（tokenize + ast）、`json`（自研确定性扫描器）。
  新语言实现 `app.languages.base.LanguagePlugin` 协议
  （`analyze / tokenize_region / lexical / rederive`）即可注册。

## 项目结构

```
app/
  models.py            # 数据模型：Position/Range/Highlight/Symbol/Diagnostic/...
  errors.py            # 可预期错误（语言不支持等）
  config.py            # ServiceConfig
  service.py           # 缓存、并发合并、超时、降级、增量编排
  incremental.py       # 受影响范围计算与高亮拼接
  server.py            # FastAPI HTTP 层
  languages/
    base.py            # 语言插件协议
    registry.py        # 语言注册表
    python_lang.py     # Python 插件
    json_lang.py       # JSON 插件
tests/                 # 43 项自动化测试
```

## 测试覆盖

- `test_highlight.py`：不重叠、可还原、确定性、并发一致
- `test_outline.py`：嵌套、排序、重名、匿名符号、JSON 键
- `test_format.py`：幂等、语义保持（AST 等价）、多行字符串保护、重复键
- `test_incremental.py`：增量==全量（含随机模糊测试）、受影响范围
- `test_service.py`：并发合并、缓存无陈旧、容量淘汰、取消隔离、超时、
  多语言切换隔离、动态注册语言
- `test_fault_tolerance.py`：语法错误、未知语言、非法编码、超大文件
- `test_server.py`：HTTP 端到端
