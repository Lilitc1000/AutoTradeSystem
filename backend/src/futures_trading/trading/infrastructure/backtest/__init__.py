"""回测适配器包：BacktestBroker + SlippageModel + 时钟/行情重放/引擎。

port-adapter 的回测侧（设计文档 7.2）：
- BacktestClock 实现 ClockPort：虚拟时钟，防未来函数的根基
- BacktestMarketData 实现 MarketDataPort：多品种时间轴重放
- BacktestBroker：虚拟账户 + 模拟撮合（OrderPort 语义）
- BacktestEngine：事件驱动主循环
"""
