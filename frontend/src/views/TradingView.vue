<script setup lang="ts">
/** 交易监控：持仓 / 订单 / 成交三列表（REST 快照）。 */
import { computed, h, onMounted, onUnmounted } from "vue";
import { storeToRefs } from "pinia";
import { NCard, NDataTable, NSpin, NTag } from "naive-ui";
import { useTradingStore } from "../stores/trading";

const store = useTradingStore();
const { positions, orders, trades, loading } = storeToRefs(store);

const statusType: Record<string, "success" | "error" | "warning" | "info" | "default"> = {
  FILLED: "success",
  CANCELLED: "default",
  REJECTED: "error",
  PARTIAL: "warning",
  SUBMITTED: "info",
  PENDING: "info",
};

const positionColumns = [
  { title: "合约", key: "contract" },
  { title: "方向", key: "direction" },
  { title: "手数", key: "quantity" },
  { title: "均价", key: "avg_price" },
  { title: "更新时间", key: "updated_at", width: 200 },
];

const orderColumns = [
  { title: "订单号", key: "order_id", width: 200 },
  { title: "合约", key: "contract" },
  { title: "方向", key: "direction" },
  { title: "开平", key: "offset" },
  { title: "手数", key: "quantity" },
  { title: "限价", key: "limit_price" },
  {
    title: "状态",
    key: "status",
    width: 110,
    render: (row: { status: string }) =>
      h(NTag, { type: statusType[row.status] ?? "default", size: "small" }, () => row.status),
  },
];

const tradeColumns = [
  { title: "订单", key: "order_pk" },
  { title: "手数", key: "quantity" },
  { title: "价格", key: "price" },
  { title: "时间", key: "ts", width: 200 },
];

const tradeRows = computed(() =>
  trades.value.map((t) => ({
    ...t,
    contract: orders.value.find((o) => o.id === t.order_pk)?.contract ?? "-",
  })),
);

onMounted(() => void store.refresh());
onUnmounted(() => store.stopStream());
</script>

<template>
  <n-spin :show="loading">
    <n-card title="持仓" style="margin-bottom: 16px">
      <n-data-table :columns="positionColumns" :data="positions" :bordered="false" size="small" />
    </n-card>

    <n-card title="订单（最新 100）" style="margin-bottom: 16px">
      <n-data-table :columns="orderColumns" :data="orders" :bordered="false" size="small" />
    </n-card>

    <n-card title="成交明细（最新 100）">
      <n-data-table :columns="tradeColumns" :data="tradeRows" :bordered="false" size="small" />
    </n-card>
  </n-spin>
</template>
