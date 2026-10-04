# codeview

面向源码的高亮、大纲与格式化服务（Python，FastAPI）。

- 语义高亮：区间完整覆盖原文、互不重叠、可还原、并发确定。
- 符号大纲：名称/种类/范围，嵌套与排序确定，重名与匿名符号有明确规则。
- 规范化格式化：幂等且不改变语义。
- 增量处理：只重算受影响区域，结果与全量重算逐条一致。
- 容错降级：语法错误/非法编码/超大文件/超时均有可区分原因与行列定位。
- 并发合并 + 有界 LRU 缓存：同内容合并为一次计算，超时/取消不污染缓存。
- 多语言：`python` / `json` / `plaintext`，语言间相互隔离、缺失语言显式报错。

## 运行

```bash
python main.py            # 启动 FastAPI（默认 :8000）
python -m pytest -q       # 运行测试
```

## 接口

- `GET /health`
- `POST /analyze`  body: `{"language": "python", "content": "..."}`
- `POST /edit`     body: `{"language", "new_content", "edit_start",
  "edit_end_old", "edit_end_new", "old_content"?, "session_id"?}`

详细处理语义、降级策略与配置项见 [`docs/SEMANTICS.md`](docs/SEMANTICS.md)。
