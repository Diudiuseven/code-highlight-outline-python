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

- 区间为零基 `(line, col)` 半开区间，按 `(start, end)` 排序，**互不重叠、无缝隙**。
- **高亮区间完整铺满整份源码**：把区间按起始位置排序后依次切出原文再拼接，
  结果与提交源码**逐字一致**——空格、制表符、换行、缩进以及末尾是否有换行
  全部被某个区间覆盖（词法之外的字符归入 `text` 类别）。可通过
  `tests/conftest.py::assert_partitions` 验证。
- 类别为词法类别：`keyword / identifier / string / number / comment /
  operator / punctuation / literal / text / unknown`；派生类别
  （`class.declaration`、`function.declaration`）由 AST 在词法结果上**原位升级**
  产生，不引入重叠。Python 3.12+ 的 f-string 被拆成
  `FSTRING_START/MIDDLE/END`，这些片段统一标为 `string`，跨行部分同样受
  格式化器保护。
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
  - **看起来像数字 / 布尔 / null 的字符串仍是字符串**：`"123"`、`"-42"`、
    `"1e3"`、`"true"`、`"false"`、`"null"` 等格式化后依然带引号，重新解析
    后类型与取值与原文完全一致。实现上数字与 `true/false/null/Infinity/NaN`
    在解析阶段标记为内部的 `RawLiteral`（`str` 的子类型），渲染时仅对
    `RawLiteral` 去引号；普通字符串（普通 `str`）一律 `json.dumps` 加引号，
    因此两类值不可能混淆。
  - 数字书写形式原样保留，键的先后顺序与重复键原样保留；同一份输入重复
    格式化任意次结果稳定不变。

### 4. 增量处理

- 高亮区间是整份源码的无缝划分，因此拼接在**绝对偏移**上进行：把编辑区域
  向外扩张，直到没有旧 token 跨越两个行对齐的切点；仅对新文本中对应的行
  区域重新词法分析，区域两侧的 token 分别原样复用与按行号差值平移。
- 仅当区域能在干净词法状态下重新分析、且拼接结果能**逐字还原整份新源码**
  时才采用增量结果；否则自动回退整份重算。大纲与格式化围绕拼接后的高亮
  重算（`rederive`）——**增量结果与整份重算逐条一致**（高亮、大纲、格式化、
  诊断、`degraded` 全部相同，两门语言均有随机编辑模糊测试）。
- 响应中的 `affected` 字段说明受影响范围（新文本坐标、行对齐），并随被
  编辑波及的跨行 token（如多行字符串）扩张；`end` 取最后一条受影响行的
  行末（闭区间语义）。

### 5. 解析容错（降级策略）

请求永不因内容问题整体失败，而是降级并给出可区分的 `diagnostics[].code`
（定位精确到行列）：

| code | 触发 | 降级行为 |
| --- | --- | --- |
| `syntax-error` | 词法/语法错误 | 返回已成功部分的高亮（仍完整铺满源码），大纲缺失时为空，格式化退化为仅换行符规范化 |
| `invalid-encoding` | 非法 UTF-8 | 以 U+FFFD 替换后继续分析，诊断给出首个非法字节的行列 |
| `file-too-large` | 超过 `max_file_size` | 不做分析，原文作为 `formatted` 返回，高亮/大纲为空 |
| `timeout` | 超过 `timeout_seconds` | 返回未处理文本，**不写入缓存** |
| `language-not-supported` | 未知/未启用语言 | 请求失败（HTTP 400），**明确报错而非静默降级** |

> 所有降级结果（`degraded=true`，含语法错误、非法编码、超大文件、超时）
> **一律不写入缓存**，因此坏输入既不会污染后续正确输入，也不会被错误地
> 当作成功结果复用；正确输入照常按内容寻址缓存。

### 6. 并发合并与缓存

- 相同 `(语言, 内容 SHA-256)` 的并发请求合并为一次计算（single-flight）。
- 缓存按内容寻址（LRU，容量 `cache_capacity`），内容变了键就变，
  **不会返回陈旧内容**；只有非降级结果会被缓存（在共享任务完成回调中
  统一写入），超时/语法错误/非法编码/超大等降级结果不入缓存。
- 单个调用方取消通过 `asyncio.shield` 隔离，不会取消共享计算、不污染缓存、
  不留中间状态；即使唯一的等待者中途取消，计算完成后其他等待者与后续
  请求仍能拿到同一个完整结果。

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
  coverage.py          # 用词法 token 铺满整份源码（缝隙补 text 类别）
  service.py           # 缓存、并发合并、超时、降级、增量编排
  incremental.py       # 偏移量拼接、受影响范围、整份一致性回退
  server.py            # FastAPI HTTP 层
  languages/
    base.py            # 语言插件协议
    registry.py        # 语言注册表
    python_lang.py     # Python 插件（tokenize + ast，含 3.12+ f-string）
    json_lang.py       # JSON 插件（RawLiteral 区分字符串与裸字面量）
tests/                 # 自动化测试
```

## 测试覆盖

- `test_highlight.py`：不重叠、可还原、确定性、并发一致
- `test_reconstruction.py`：**高亮逐字还原整份源码**（含空白/换行/缩进、
  无末尾换行、f-string、多行字符串、降级输入）
- `test_outline.py`：嵌套、排序、重名、匿名符号、JSON 键
- `test_format.py`：幂等、语义保持（AST 等价）、多行字符串保护、重复键
- `test_json_semantics.py`：**像数字/布尔/null 的字符串格式化后类型不变**、
  数字书写形式、键序、重复键取值类型、多次格式化稳定
- `test_incremental.py` / `test_incremental_consistency.py`：
  增量==全量（两门语言的随机编辑与链式编辑模糊测试）、逐字还原、受影响范围
- `test_service.py` / `test_coalesce_and_degrade.py`：不同并发度合并、
  缓存无陈旧、容量淘汰、取消隔离（共享任务仍完成并缓存）、超时、
  降级结果不入缓存、多语言切换隔离、动态注册语言
- `test_fault_tolerance.py`：语法错误、未知语言、非法编码、超大文件
- `test_server.py`：HTTP 端到端

## 本地验证

```bash
# 安装依赖（需要 Python 3.14）
uv sync

# 跑全部测试
uv run pytest tests -q

# 只验证本说明强调的三类不变量
uv run pytest tests/test_reconstruction.py \
               tests/test_json_semantics.py \
               tests/test_incremental_consistency.py -q

# 启动服务
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

手工校验高亮还原：对一次 `/analyze` 的返回，把 `highlights` 按
`range.start` 排序，用每个区间切出原文再拼接，应与请求体逐字相同
（测试里的 `assert_partitions(source, highlights)` 即做此校验，同时检查
不重叠、无缝隙）。手工校验 JSON 语义保持：对 `formatted` 与原文分别
`json.loads`，逐键比较 `type` 与值应完全一致，且 `"123"`、`"true"`、
`"null"` 等仍是字符串。
