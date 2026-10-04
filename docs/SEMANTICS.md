# CodeView 处理语义与配置说明

面向源码的 **高亮 / 大纲 / 格式化** 服务。输入为「源码文本 + 目标语言」，输出：

- `highlights`：带语义类别的半开区间 `[start, end)`（字符偏移 + 行列）；
- `outline`：带名称、种类、完整范围与选择范围的嵌套符号树；
- `formatted`：规范化后的文本；
- `status` / `reasons` / `diagnostics`：结果状态、原因码与精确定位。

所有偏移均为 **解码后的 Unicode 字符偏移**，行列中行为 1-based、列为 0-based。

## 1. 高亮（Highlights）

- 区间按 `start` 排序，**互不重叠且完整覆盖整个文档**：每个字符恰好属于一个
  区间，拼接 `text[start:end]` 可逐条还原原文。
- 未被任何词法单元覆盖的字符使用兜底类别 `text`。
- 当两个区间**完全重合**时按固定优先级去重（AST 语义优先于词法通用类别，
  例如 `parameter` 覆盖 `identifier`）；任何残余重叠都视为后端 bug 并抛
  `ValueError`，绝不静默吞掉。
- 纯函数、无共享可变状态：同一份输入在任意并发度、任意请求顺序下结果
  **逐条一致**（有测试强制 8/6 线程并发比对序列化结果）。

Python 类别：`keyword`、`string`、`number`、`comment`、`operator`、
`punctuation`、`function`、`class`、`decorator`、`parameter`、
`builtin`、`constant`（全大写赋值名）、`identifier`、`text`。

JSON 类别：`property`（对象键）、`string`、`number`、`keyword`
（`true`/`false`/`null`）、`punctuation`、`text`。

## 2. 大纲（Outline）

- 每个符号含 `name`、`kind`、`range`（整个构造，Python 含装饰器）、
  `selection_range`（名字所在区间）、`children`。
- **嵌套**只依据词法包含关系；**排序**键为
  `(range.start, range.end, kind, name)`，因此结果确定。
- **重名符号全部保留**，用各自的 `range` 区分（例如两个同名函数都会出现）。
- **匿名符号**使用固定名字 `"<anonymous>"`：JSON 的匿名对象/数组。JSON
  数组元素以其下标字符串 `"0".."n-1"` 命名；对象成员以键名命名。
- 匿名容器不会在大纲里产生多余层级：对象成员/数组元素若是容器，其成员会被
  直接展开为该属性/元素的 `children`。

## 3. 格式化（Formatting）

所有操作都是**保持语义**的幂等规范化，再次格式化结果不变。

Python（区域内，跨行字符串内部受保护、不改动）：

1. CRLF/CR → LF；
2. 删除行尾水平空白（行尾是续行反斜杠 `\` 时保留空白）；
3. 连续空行折叠为至多一个空行；
4. 首区域去掉前导空行，末区域去掉尾部空行；非空文档保证恰好一个结尾换行；
5. 三引号/多行 f-string 内部的空行与空白完全不动。

JSON：有效时 `json.dumps(indent=2, ensure_ascii=False)` 并加结尾换行
（保留键顺序、原样保留非 ASCII、数字往返不改变语义）；**无效 JSON 不伪造
内容**，只做换行归一化并返回 `syntax_error`。

Plaintext：仅换行归一化，并为非空文本补一个结尾换行。

## 4. 增量处理（Incremental）

文档被切分为若干**可独立重解析的区域（region）**，区域边界是模块级空行段。
边界保证不会落在词法 token 内部或缩进块内部，因此每个区域包含完整语句。

- 编辑后只重算「与编辑区间相交」的区域（当编辑触及文档尾部/可能改变分区时，
  末区域也会保守重算）；其余区域按**区域文本 + first/last 位置**从会话缓存
  复用。
- 全量与增量走**同一条装配路径**：全量＝分区后逐区域分析再装配；增量＝复用
  部分区域后用相同方式装配。因此增量的高亮/大纲/格式化/诊断与整份重算
  **逐条相同**（含随机编辑、语法错误源的对拍测试）。
- 受影响范围通过 `affected_ranges`（重算区域的 `[start,end)` 列表）、
  `region_count` 与 `regions_recomputed` 返回。
- JSON / Plaintext 为单区域，编辑即整份重算（结果天然一致）。

会话：可用 `session_id` 让服务记住上一版文本与区域分析；也可在请求里直接给
`old_content`。若编辑描述与新旧文本不一致（前缀/后缀对不上、越界、会话过期），
**自动回退为整份重算**，不报错、不产生错误结果。

## 5. 容错降级（Fault Tolerance）

请求整体不失败，用 `status` 区分：

- `ok`：完整结果；
- `degraded`：HTTP 200，能保证的部分照常返回，`reasons` 非空且每个问题带
  行列定位的 `diagnostics`。Python/JSON 语法错误时：尽力给出高亮（错误前的
  词法单元）、空/部分大纲，格式化按既定安全策略执行。
- `error`：请求无法服务（见下），HTTP 4xx/5xx，带可区分 `reason`。

原因码：

| reason               | 触发场景                          | HTTP |
|----------------------|-----------------------------------|------|
| `syntax_error`       | 源码语法错误（降级，不 error）    | 200  |
| `unknown_language`   | 语言未启用/未注册                 | 400/500 |
| `illegal_encoding`   | 非 UTF-8 字节或带 BOM/base64 非法 | 400  |
| `file_too_large`     | 解码后字符数超过上限              | 413  |
| `timeout`            | 计算超过 `timeout_ms`             | 504  |

定位：诊断范围为半开 `[start,end)`，含 `start_pos`/`end_pos` 的行列。

## 6. 并发合并与缓存（Coalescing & Cache）

- **合并**：相同（语言, 内容哈希）的并发计算只执行一次，所有等待者拿到同一结果
  或同一异常。等待者被取消（超时）只放弃自己的等待，不会中断共享计算。
- **缓存**：以 `sha256(utf-8)` 为键的有界 LRU；只有**成功**结果才入缓存，
  生产函数抛异常不留任何条目。
- **不陈旧**：键包含完整内容哈希，编辑后内容变化即新键，永不返回旧内容。
- **超时/取消不污染**：异步派发为合并计算设置共享截止时间；超时后即使后台线程
  稍后完成，也因截止时间已过而**不写缓存、不更新会话**。同步 API 不设超时。

## 7. 配置（Configuration）

`Settings`（也可用环境变量覆盖）：

| 字段                | 环境变量                       | 默认值        | 含义 |
|---------------------|--------------------------------|---------------|------|
| `languages`         | `CODEVIEW_LANGUAGES`（逗号分隔）| python,json,plaintext | 启用语言集合 |
| `max_file_size`     | `CODEVIEW_MAX_FILE_SIZE`       | 2,000,000     | 单文件解码后字符上限 |
| `cache_capacity`    | `CODEVIEW_CACHE_CAPACITY`      | 256           | 分析结果 LRU 上限 |
| `session_capacity`  | `CODEVIEW_SESSION_CAPACITY`    | 1024          | 编辑会话 LRU 上限 |
| `timeout_ms`        | `CODEVIEW_TIMEOUT_MS`          | 5000          | 单次计算超时（毫秒） |

- **语言隔离**：注册表按语言 id 独立持有无状态后端；注册/替换某个语言不影响
  其他语言的结果（有切换隔离测试）。
- **缺失语言显式报错**：请求未启用语言返回 `unknown_language`，绝不静默降级。

## 8. HTTP 接口

- `GET /health`：服务状态、已注册与已启用语言、大小上限。
- `POST /analyze`：`{language, content}`，或用 `content_base64` 提交原始字节
  以触发非法编码检测。
- `POST /edit`：
  `{language, new_content, edit_start, edit_end_old, edit_end_new,
  old_content?, session_id?}`，表示
  `old[edit_start:edit_end_old]` 被替换为 `new[edit_start:edit_end_new]`。

## 9. 新增语言后端

实现无状态协议（`codeview/backend.py`）：

- `language_id`；
- `analyze_region(text, is_first, is_last) -> Analysis`；
- `split_regions(text) -> tuple[Region, ...]`（不支持增量则返回单区域）；
- `analyze(text)` 复用 `incremental.analyze_full`。

然后在 `default_registry()` 注册即可；语法错误必须返回 `syntax_error`
诊断而不是抛异常。
