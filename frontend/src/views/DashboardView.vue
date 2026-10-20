<script setup lang="ts">
/** 总览：系统健康 + 账户摘要 + 当日信号流（SSE）。 */
import { onMounted, onUnmounted } from "vue";
import { storeToRefs } from "pinia";
import { NAlert, NCard, NGi, NGrid, NLog, NSpace, NSpin, NTag } from "naive-ui";
import { useTradingStore } from "../stores/trading";

const store = useTradingStore();
const { summary, heartbeats, signalFeed, fillFeed, loading, error } = storeToRefs(store);

function heartbeatAlive(entry: unknown): boolean {
  if (!entry || typeof entry !== "object") return false;
  return Object.keys(entry as object).length > 0;
}

onMounted(() => {
  void store.refresh();
  store.startStream();
});
onUnmounted(() => store.stopStream());
</script>

<template>
  <div>
    <n-spin :show="loading">
      <n-alert v-if="error" type="error" :title="error" style="margin-bottom: 12px" />

      <n-grid :cols="4" :x-gap="12" style="margin-bottom: 16px">
        <n-gi>
          <n-card title="账户权益">
            <div class="metric">{{ summary?.equity ?? "-" }}</div>
          </n-card>
        </n-gi>
        <n-gi>
          <n-card title="当日已实现">
            <div class="metric" :class="{ profit: Number(summary?.realized) > 0, loss: Number(summary?.realized) < 0 }">
              {{ summary?.realized ?? "-" }}
            </div>
          </n-card>
        </n-gi>
        <n-gi>
          <n-card title="浮动盈亏">
            <div class="metric" :class="{ profit: Number(summary?.unrealized) > 0, loss: Number(summary?.unrealized) < 0 }">
              {{ summary?.unrealized ?? "-" }}
            </div>
          </n-card>
        </n-gi>
        <n-gi>
          <n-card title="持仓合约数">
            <div class="metric">{{ summary?.position_count ?? "-" }}</div>
          </n-card>
        </n-gi>
      </n-grid>

      <n-card title="Worker 心跳" style="margin-bottom: 16px">
        <n-space>
          <n-tag
            v-for="(entry, name) in heartbeats"
            :key="name"
            :type="heartbeatAlive(entry) ? 'success' : 'error'"
          >
            {{ name }}: {{ heartbeatAlive(entry) ? "在线" : "离线" }}
          </n-tag>
        </n-space>
      </n-card>

      <n-grid :cols="2" :x-gap="12">
        <n-gi>
          <n-card title="信号流（实时）">
            <n-log :rows="12" :log="signalFeed.map((s) => JSON.stringify(s)).join('\n')" style="height: 260px" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card title="成交流（实时）">
            <n-log :rows="12" :log="fillFeed.map((f) => JSON.stringify(f)).join('\n')" style="height: 260px" />
          </n-card>
        </n-gi>
      </n-grid>
    </n-spin>
  </div>
</template>

<style scoped>
.metric {
  font-size: 24px;
  font-weight: 600;
}
.profit {
  color: #d03050;
}
.loss {
  color: #18a058;
}
</style>
