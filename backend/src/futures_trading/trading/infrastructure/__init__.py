"""回测基础设施：BacktestBroker + SlippageModel。

这是 port-adapter 的"回测侧适配器"：实现与 CTPAdapter 相同的
OrderPort 语义，但完全本地运行、零外部依赖（设计文档 7.2）。
"""
