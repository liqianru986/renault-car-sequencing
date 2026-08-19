<!-- 架构文档：记录分层、依赖方向、接口边界和重要设计决策。 -->

# Architecture

```text
CLI / scripts
    ↓
application
    ↓
domain ← evaluation ← algorithms
    ↑                  ↑
infrastructure         exact adapters
```

`domain` 不依赖文件系统、Checker、Gurobi 或具体搜索算法。

