// CTP 交易链路对照探针（官方 so，无 patch）：认证 → 登录 → 查询资金账户。
// 用途：区分「SimNow 交易侧问题」与「openctp td 绑定问题」——
// Python 报单探针连 30001 出现 OnFrontConnected 不触发 + malloc 堆损坏，
// 若本探针（同账号同地址、官方 so）能正常认证登录查询，则问题在 openctp
// Python 绑定层；否则是 SimNow 交易侧/账号权限。
// 构建：run.sh 同款（g++ -I /opt/ctp 链官方 so）。
// 运行：./td_probe <BrokerID> <UserID> <Password> [FrontAddr]

#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <unistd.h>

#include "ThostFtdcTraderApi.h"

class LoginSpi : public CThostFtdcTraderSpi {
public:
    CThostFtdcTraderApi* api;
    char broker[32];
    char user[64];
    char pass[64];
    char appid[64];
    char authcode[64];
    bool front_connected = false;
    bool login_done = false;
    int account_queried = 0;

    void OnFrontConnected() override {
        front_connected = true;
        std::printf("[td-probe] 前置连接成功\n");
        std::fflush(stdout);
        CThostFtdcReqAuthenticateField req{};
        std::strncpy(req.BrokerID, broker, sizeof(req.BrokerID) - 1);
        std::strncpy(req.UserID, user, sizeof(req.UserID) - 1);
        std::strncpy(req.AppID, appid, sizeof(req.AppID) - 1);
        std::strncpy(req.AuthCode, authcode, sizeof(req.AuthCode) - 1);
        api->ReqAuthenticate(&req, 1);
    }

    void OnFrontDisconnected(int nReason) override {
        std::printf("[td-probe] 前置断开 reason=%d\n", nReason);
        std::fflush(stdout);
    }

    void OnRspAuthenticate(CThostFtdcRspAuthenticateField* pRspAuthenticateField,
                           CThostFtdcRspInfoField* pRspInfo, int nRequestID, bool bIsLast) override {
        if (pRspInfo && pRspInfo->ErrorID != 0) {
            std::printf("[td-probe] 认证失败 ErrorID=%d %s\n", pRspInfo->ErrorID, pRspInfo->ErrorMsg);
            std::fflush(stdout);
            return;
        }
        std::printf("[td-probe] 认证成功\n");
        std::fflush(stdout);
        CThostFtdcReqUserLoginField req{};
        std::strncpy(req.BrokerID, broker, sizeof(req.BrokerID) - 1);
        std::strncpy(req.UserID, user, sizeof(req.UserID) - 1);
        std::strncpy(req.Password, pass, sizeof(req.Password) - 1);
        api->ReqUserLogin(&req, 2);
    }

    void OnRspUserLogin(CThostFtdcRspUserLoginField* pRspUserLogin,
                        CThostFtdcRspInfoField* pRspInfo, int nRequestID, bool bIsLast) override {
        if (pRspInfo && pRspInfo->ErrorID != 0) {
            std::printf("[td-probe] 登录失败 ErrorID=%d %s\n", pRspInfo->ErrorID, pRspInfo->ErrorMsg);
            std::fflush(stdout);
            return;
        }
        login_done = true;
        std::printf("[td-probe] 登录成功 TradingDay=%s\n", pRspUserLogin->TradingDay);
        std::fflush(stdout);
        // 查询资金账户
        CThostFtdcQryTradingAccountField req{};
        std::strncpy(req.BrokerID, broker, sizeof(req.BrokerID) - 1);
        std::strncpy(req.InvestorID, user, sizeof(req.InvestorID) - 1);
        api->ReqQryTradingAccount(&req, 3);
    }

    void OnRspQryTradingAccount(CThostFtdcTradingAccountField* pTradingAccount,
                                CThostFtdcRspInfoField* pRspInfo, int nRequestID, bool bIsLast) override {
        if (pTradingAccount == nullptr) {
            std::printf("[td-probe] 资金查询无数据（bIsLast=%d）\n", bIsLast ? 1 : 0);
            std::fflush(stdout);
            return;
        }
        account_queried++;
        std::printf("[td-probe] 资金账户: Available=%s Balance=%s\n",
                    pTradingAccount->Available, pTradingAccount->Balance);
        std::fflush(stdout);
    }

    void OnRspError(CThostFtdcRspInfoField* pRspInfo, int nRequestID, bool bIsLast) override {
        if (pRspInfo) {
            std::printf("[td-probe] OnRspError ErrorID=%d %s\n", pRspInfo->ErrorID, pRspInfo->ErrorMsg);
            std::fflush(stdout);
        }
    }
};

int main(int argc, char** argv) {
    if (argc < 4) {
        std::printf("用法: %s <BrokerID> <UserID> <Password> [FrontAddr]\n", argv[0]);
        return 2;
    }
    const char* front = argc > 4 ? argv[4] : "tcp://182.254.243.31:30001";

    LoginSpi spi{};
    std::strncpy(spi.broker, argv[1], sizeof(spi.broker) - 1);
    std::strncpy(spi.user, argv[2], sizeof(spi.user) - 1);
    std::strncpy(spi.pass, argv[3], sizeof(spi.pass) - 1);
    std::strncpy(spi.appid, "simnow_client_test", sizeof(spi.appid) - 1);
    std::strncpy(spi.authcode, "0000000000000000", sizeof(spi.authcode) - 1);

    std::printf("[td-probe] 创建交易 API，前置 %s ...\n", front);
    CThostFtdcTraderApi* api = CThostFtdcTraderApi::CreateFtdcTraderApi("/tmp/td_probe_flow/");
    spi.api = api;
    api->RegisterSpi(&spi);
    api->RegisterFront(const_cast<char*>(front));
    api->SubscribePrivateTopic(THOST_TERT_RESUME);
    api->SubscribePublicTopic(THOST_TERT_RESUME);
    api->Init();

    for (int i = 0; i < 250 && !spi.login_done; ++i) {
        usleep(100000);
    }
    if (!spi.login_done) {
        std::printf("[td-probe] 超时：25 秒未登录（front_connected=%d）\n", spi.front_connected ? 1 : 0);
    } else {
        for (int i = 0; i < 100 && spi.account_queried == 0; ++i) {
            usleep(100000);
        }
    }
    api->Release();
    std::printf("[td-probe] 退出 login=%d account=%d\n", spi.login_done ? 1 : 0, spi.account_queried);
    return spi.login_done ? 0 : 1;
}
