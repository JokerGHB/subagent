"""统一日志配置：全部走 stderr。

学习点（MCP 关键概念）：MCP 的 stdio 传输里，stdout 是【协议通道】——
客户端靠它接收 JSON-RPC 消息。任何 print 都会混进协议流、把通信干崩。
所以 agent 系统的日志必须走 stderr。CLI 模式（run.py）也统一用它，保持日志格式一致。
"""
import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    )
    logging.basicConfig(handlers=[handler], level=level, force=True)
