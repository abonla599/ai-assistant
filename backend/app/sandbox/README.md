# 安全沙箱模块 (Sandbox)

## 简介
提供 Python 和 JavaScript 的安全代码执行环境，基于 Docker 容器实现隔离。

## 使用方法
``python
from app.sandbox.sandbox_manager import SandboxManager
sandbox = SandboxManager()
esult = sandbox.run_code("print(1+1)", language="python")
# result["stdout"] -> "2\n"
# result["error"]  -> None (成功) 或 错误信息 (失败)
``` 

## 返回结构
| 字段 | 说明 |
|------|------|
| stdout | 标准输出 |
| error | None=成功 |
| execution_time | 耗时(秒) |
| stats | 运行统计 |

## 安全特性
- 非 root 用户
- 网络禁用
- 根文件系统只读
- 内存限制 64MB
- 超时控制（默认10秒）

## 维护者
王翔宇
