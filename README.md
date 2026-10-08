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

### 1. 高亮（区间必须完整铺满源码）

- 区间为零基 `(line, col)` 半开区间，按 `(start, end)` 排序，**互不重叠、
  无缝隙**。
- **逐字还原（核心验收语义）**：把返回区间按起始位置排序后，依次从原文
  切出 `source[range]` 再拼接，结果必须与提交的源码**逐字一致**——空格、
  Tab、CR/LF 换行、缩进、空行以及容错路径下无法词法分析的尾巴都必须被某个
  区间覆盖。词法器只产出语义 token，未被认领的字符统一以
  `text`（填充/平凡文本）类别补齐；拼接后相邻的同类别区间会被规范化为同一
  个区间，因此整份重算与增量拼接得到的区间列表逐条相同。
- 类别为词法类别：`keyword / identifier / string / number / comment /
  operator / punctuation / literal / text`；派生类别（`class.declaration`、
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
- JSON 使用确定性美化器，**绝不改变文档语义**：
  - **字符串永远是字符串**：即使字符串内容看起来像数字（`"123"`、
    `"007"`、`"1.50"`、`"1e3"`）或像布尔/空值（`"true"`、`"false"`、
    `"null"`、`"NaN"`、`"Infinity"`），格式化后仍带引号，重新解析后其
    类型仍为字符串且取值不变。真正的数字/常量（`123`、`true`、`null`）
    则保持数字/布尔/null 类型。解析期数字与常量通过内部 `_Raw` 包装类型
    与普通字符串区分，从根上杜绝歧义。
  - **数字字面量保持原始书写形式**（`1e3` 不会被改写为 `1000.0`，
    `100.0`、`1.00`、`0.0001` 原样保留）。
  - **键的先后顺序与重复键原样保留**（`{"z":1,"z":2}` 仍是两个 `z`）。
  - 字符串按规范转义形式（`ensure_ascii=False`）输出；**幂等**：连续格式化
    任意次结果稳定不变。

### 4. 增量处理

- 编辑后仅对**受影响行区域**重新词法分析，区域外的高亮 token 经位置平移
  后复用；大纲与格式化围绕拼接后的高亮结果重算（`rederive`）。
- 区域按**绝对偏移**在旧的“已铺满” token 流上切割，且扩张到**不动点**
  （扩张后可能继续撞上跨越多行的 token，例如多行字符串），跨越切点的
  token 被裁剪到切点而不是整条丢弃。拼接结果再以**新文本**做一次平铺
  （`tile_highlights`：裁剪越界产物、补齐缝隙、合并相邻同类区间），保证
  增量区间同样完整铺满新源码。
- 仅当旧结果未降级、新旧切点均处于“干净状态”且新文本无语法错误时才采用
  增量结果，否则自动回退整份重算——**增量结果与整份重算逐条一致**
  （高亮、大纲、格式化，含数百条含多行替换的随机编辑模糊测试）。
- 响应中的 `affected` 字段说明受影响范围（新文本坐标，行对齐）：增量成功
  时覆盖实际重新词法分析并被波及的行区域；回退整份重算时覆盖整份文本。

### 5. 解析容错（降级策略）

请求**永不因内容问题整体失败**，而是降级返回可用的部分结果，并给出可区分
的 `diagnostics[].code`（定位精确到行列）：

| code | 触发 | 降级行为 |
| --- | --- | --- |
| `syntax-error` | 词法/语法错误 | 返回已成功部分并以 `text` 铺满整份源码的高亮，大纲缺失时为空，格式化退化为仅换行符规范化（Python）/保守换行规范化（JSON） |
| `invalid-encoding` | 非法 UTF-8 | 以 U+FFFD 替换后继续分析，诊断给出首个非法字节的行列 |
| `file-too-large` | 超过 `max_file_size` | 不做分析，原文作为 `formatted` 返回，高亮/大纲为空 |
| `timeout` | 超过 `timeout_seconds` | 返回未处理文本，**不写入缓存** |
| `analysis-error` | 插件内部意外异常 | 兜底捕获，返回未处理文本与诊断，请求不失败，不写缓存 |
| `language-not-supported` | 未知/未启用语言 | 请求失败（HTTP 400），**明确报错而非静默降级** |

**降级结果一律不写入缓存**（语法错误、非法编码、超时、插件异常均是），
因此一次坏输入不会影响后续正确输入的返回；正确输入始终重新计算并按内容
寻址缓存。

### 6. 并发合并与缓存

- 相同 `(语言, 内容 SHA-256)` 的并发请求合并为一次计算（single-flight），
  任意并发度（1/2/8/32/64…）与请求顺序下所有等待者拿到**同一个结果对象**，
  逐条一致。
- 缓存按内容寻址（LRU，容量 `cache_capacity`），内容变了键就变，
  **不会返回陈旧内容**。
- 缓存写入只在共享计算**完成回调**中发生一次：只有完整、未降级的成功结果
  才入缓存；超时/取消/降级结果绝不入缓存。因此即使所有等待者都提前取消，
  被 `asyncio.shield` 保护的共享计算仍会跑完，但最多只把**完整**结果放入
  缓存，绝不会出现残缺中间态。
- 单个调用方取消通过 `asyncio.shield` 隔离，不会取消共享计算、不会污染
  缓存、不留 in-flight 残留，其他等待者仍能拿到完整结果。

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
  incremental.py       # 受影响范围计算、不动点区域扩张、偏移切割与高亮拼接
  server.py            # FastAPI HTTP 层
  languages/
    base.py            # 语言插件协议
    registry.py        # 语言注册表
    tiling.py          # 高亮平铺：去重叠、补 text 缝隙、规范化相邻同类区间
    python_lang.py     # Python 插件
    json_lang.py       # JSON 插件
tests/                 # 自动化测试（87 项）
```

## 本地验证

```bash
# 安装依赖（需要 Python >= 3.14）
uv sync

# 跑全部自动化测试
uv run pytest tests -q

# 只看本次收紧的四类验收
uv run pytest tests/test_tiling.py \
               tests/test_json_semantics.py \
               tests/test_acceptance_incremental.py \
               tests/test_acceptance_service.py -q

# 启动服务后用 curl 自检
uv run uvicorn main:app --host 127.0.0.1 --port 8000
curl -s "http://127.0.0.1:8000/languages"
curl -s -X POST "http://127.0.0.1:8000/analyze?language=json" \
     --data-binary '{"a":"true","b":1e3}'
```

“区间逐字还原源码”可直接按下式自检（测试中已固化）：

```python
result = plugin.analyze(source)
pieces = [slice_source(source, h.range) for h in
          sorted(result.highlights, key=lambda h: (h.range.start, h.range.end))]
assert "".join(pieces) == source          # 无重叠、无缝隙、逐字一致
```

## 测试覆盖

- `test_tiling.py`：**高亮完整铺满源码**——Python/JSON、空白/换行/缩进、
  无末尾换行、容错输入，区间拼接逐字还原、无重叠
- `test_json_semantics.py`：**格式化不改语义**——像数字/布尔/null 的字符串
  仍是字符串、真数字/常量类型不变、数字字面量书写形式、键顺序与重复键、幂等
- `test_acceptance_incremental.py`：**随机编辑下增量==全量**（逐条，含
  多行替换、无末尾换行）、增量区间仍逐字还原、`affected` 能解释编辑
- `test_acceptance_service.py`：**并发合并**（多并发度/混合顺序/同对象）、
  取消隔离、全员取消不留中间态、**容错降级且不写缓存**、语言门控与隔离
- `test_highlight.py`：不重叠、可还原、确定性、并发一致（基线）
- `test_outline.py`：嵌套、排序、重名、匿名符号、JSON 键
- `test_format.py`：幂等、语义保持（AST 等价）、多行字符串保护、重复键
- `test_incremental.py`：增量==全量（随机模糊）、受影响范围
- `test_service.py`：并发合并、缓存无陈旧、容量淘汰、取消隔离、超时、
  多语言切换隔离、动态注册语言
- `test_fault_tolerance.py`：语法错误、未知语言、非法编码、超大文件
- `test_server.py`：HTTP 端到端
