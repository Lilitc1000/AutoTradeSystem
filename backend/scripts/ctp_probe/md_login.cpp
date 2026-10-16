// CTP 登录 + 行情订阅探针（C++ 对照验证，M2-d 前置）。
// 用途：验证 SimNow 行情推送（绕开 openctp Python 层）。
// 构建：run.sh（g++ -I /opt/ctp，链官方 so）。
// 运行：run.sh [FrontAddr]

#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <unistd.h>

#include "ThostFtdcMdApi.h"
#include "ThostFtdcTraderApi.h"

class LoginSpi : public CThostFtdcMdSpi {
public:
    CThostFtdcMdApi* api;
    char broker[32];
    char user[64];
    char pass[64];
    int request_id = 0;
    bool front_connected = false;
    bool login_done = false;
    int tick_count = 0;

    void OnFrontConnected() override {
        front_connected = true;
        std::printf("[probe] 前置连接成功\n");
        std::fflush(stdout);
        CThostFtdcReqUserLoginField req{};
        std::strncpy(req.BrokerID, broker, sizeof(req.BrokerID) - 1);
        std::strncpy(req.UserID, user, sizeof(req.UserID) - 1);
        std::strncpy(req.Password, pass, sizeof(req.Password) - 1);
        api->ReqUserLogin(&req, ++request_id);
    }

    void OnFrontDisconnected(int nReason) override {
        std::printf("[probe] 前置断开 reason=%d\n", nReason);
        std::fflush(stdout);
    }

    void OnRspUserLogin(CThostFtdcRspUserLoginField* pRspUserLogin,
                        CThostFtdcRspInfoField* pRspInfo,
                        int nRequestID, bool bIsLast) override {
        login_done = true;
        if (pRspInfo && pRspInfo->ErrorID != 0) {
            std::printf("[probe] 登录失败 ErrorID=%d ErrorMsg=%s\n",
                        pRspInfo->ErrorID, pRspInfo->ErrorMsg);
            std::fflush(stdout);
            return;
        }
        std::printf("[probe] 登录成功 TradingDay=%s\n",
                    pRspUserLogin ? pRspUserLogin->TradingDay : "?");
        std::fflush(stdout);
        // 订阅行情（TEST/BTC/MINUS 为 7x24 环境模拟合约）
        char* insts[] = {(char*)"TEST", (char*)"BTC", (char*)"MINUS", (char*)"rb2601"};
        api->SubscribeMarketData(insts, 4);
    }

    void OnRspSubMarketData(CThostFtdcSpecificInstrumentField* pSpecific,
                            CThostFtdcRspInfoField* pRspInfo,
                            int nRequestID, bool bIsLast) override {
        std::printf("[probe] sub %s %s\n",
                    pSpecific ? pSpecific->InstrumentID : "?",
                    (pRspInfo && pRspInfo->ErrorID != 0) ? "FAIL" : "OK");
        std::fflush(stdout);
    }

    void OnRtnDepthMarketData(CThostFtdcDepthMarketDataField* p) override {
        tick_count++;
        if (tick_count <= 5) {
            std::printf("[probe] tick %s last=%s vol=%d time=%s.%d\n",
                        p->InstrumentID, p->LastPrice, p->Volume,
                        p->UpdateTime, p->UpdateMillisec);
            std::fflush(stdout);
        }
    }

    void OnRspError(CThostFtdcRspInfoField* pRspInfo, int nRequestID, bool bIsLast) override {
        if (pRspInfo) {
            std::printf("[probe] OnRspError ErrorID=%d ErrorMsg=%s\n",
                        pRspInfo->ErrorID, pRspInfo->ErrorMsg);
            std::fflush(stdout);
        }
    }
};

int main(int argc, char** argv) {
    if (argc < 4) {
        std::printf("用法: %s <BrokerID> <UserID> <Password> [FrontAddr]\n", argv[0]);
        return 2;
    }
    const char* front = argc > 4 ? argv[4] : "tcp://182.254.243.31:40011";

    LoginSpi spi{};
    std::strncpy(spi.broker, argv[1], sizeof(spi.broker) - 1);
    std::strncpy(spi.user, argv[2], sizeof(spi.user) - 1);
    std::strncpy(spi.pass, argv[3], sizeof(spi.pass) - 1);

    std::printf("[probe] 创建行情 API，前置 %s ...\n", front);
    CThostFtdcMdApi* api = CThostFtdcMdApi::CreateFtdcMdApi("./flow/");
    spi.api = api;
    api->RegisterSpi(&spi);
    api->RegisterFront(const_cast<char*>(front));
    api->Init();

    // 等待登录（最多 25 秒），成功后再等 50 秒收 tick
    for (int i = 0; i < 250 && !spi.login_done; ++i) {
        usleep(100000);
    }
    if (!spi.login_done) {
        std::printf("[probe] 超时：25 秒内未完成登录（front_connected=%d）\n",
                    spi.front_connected ? 1 : 0);
    } else {
        std::printf("[probe] 等待行情推送（50 秒）...\n");
        for (int i = 0; i < 500 && spi.tick_count == 0; ++i) {
            usleep(100000);
        }
    }
    api->Release();
    std::printf("[probe] 退出，共 %d tick\n", spi.tick_count);
    return spi.login_done ? 0 : 1;
}
