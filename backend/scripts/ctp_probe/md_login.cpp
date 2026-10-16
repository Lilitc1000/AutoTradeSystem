// CTP 登录连通性探针（M2-b 联调用）
// 用途：验证 SimNow 账号密码 + 前置连通性，不依赖 Python 绑定
// 构建：g++ -std=c++11 -I<ctp头文件目录> md_login.cpp -L<so目录> -lthostmduserapi_se -lthosttraderapi_se -Wl,-rpath,<so目录> -o md_login
// 运行：./md_login <BrokerID> <UserID> <Password> [FrontAddr]

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
        } else if (pRspUserLogin) {
            std::printf("[probe] 登录成功 BrokerID=%s UserID=%s TradingDay=%s\n",
                        pRspUserLogin->BrokerID, pRspUserLogin->UserID,
                        pRspUserLogin->TradingDay);
        }
        std::fflush(stdout);
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
    const char* front = argc > 4 ? argv[4] : "tcp://180.168.146.187:10131";

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

    // 等待登录结果（最多 25 秒）
    for (int i = 0; i < 250 && !spi.login_done; ++i) {
        usleep(100000);
    }
    if (!spi.login_done) {
        std::printf("[probe] 超时：25 秒内未完成登录（front_connected=%d）\n",
                    spi.front_connected ? 1 : 0);
    }
    api->Release();
    std::printf("[probe] 退出\n");
    return spi.login_done ? 0 : 1;
}
