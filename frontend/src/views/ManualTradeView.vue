<script setup lang="ts">
/** 手动下单（M3-e3）：表单 → POST /api/trading/orders → trade.order 出口。
 *  执行结果通过 SSE 频道 trade.order.status 实时回流显示。
 */
import { onMounted, onUnmounted, reactive, ref } from "vue";
import {
  NAlert, NButton, NCard, NForm, NFormItem, NInputNumber, NSelect, NSpace, NTag,
} from "naive-ui";
import type { OrderStatusEvent } from "../api/manual_order";
import { placeManualOrder } from "../api/manual_order";
import { subscribeStream } from "../api/stream";

const submitting = ref(false);
const error = ref<string | null>(null);
const lastAccepted = ref<Record<string, unknown> | null>(null);
const events = ref<OrderStatusEvent[]>([]);
let _unsubscribe: (() => void) | null = null;

const form = reactive({
  contract: "rb2701",
  direction: "LONG" as "LONG" | "SHORT",
  offset: "OPEN" as "OPEN" | "CLOSE" | "CLOSE_TODAY" | "CLOSE_YESTERDAY",
  quantity: 1,
  price_type: "LIMIT" as "LIMIT" | "MARKET",
  limit_price: 3067,
});

const directionOptions = [
  { label: "买入 LONG", value: "LONG" },
  { label: "卖出 SHORT", value: "SHORT" },
];
const offsetOptions = [
  { label: "开仓 OPEN", value: "OPEN" },
  { label: "平仓 CLOSE（非 SHFE）", value: "CLOSE" },
  { label: "平今 CLOSE_TODAY（SHFE）", value: "CLOSE_TODAY" },
  { label: "平昨 CLOSE_YESTERDAY（SHFE）", value: "CLOSE_YESTERDAY" },
];
const priceTypeOptions = [
  { label: "限价 LIMIT（SHFE 必须用）", value: "LIMIT" },
  { label: "市价 MARKET", value: "MARKET" },
];

function _onMessage(msg: { channel: string; payload: Record<string, unknown> | null }) {
  if (msg.channel !== "trade.order.status" || !msg.payload) return;
  events.value.unshift(msg.payload as unknown as OrderStatusEvent);
  if (events.value.length > 20) events.value.pop();
}

async function submit() {
  submitting.value = true;
  error.value = null;
  try {
    lastAccepted.value = await placeManualOrder({
      contract: form.contract,
      direction: form.direction,
      offset: form.offset,
      quantity: form.quantity,
      price_type: form.price_type,
      limit_price: form.price_type === "LIMIT" ? String(form.limit_price) : null,
    });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    submitting.value = false;
  }
}

onMounted(() => {
  _unsubscribe = subscribeStream(["trade.order.status"], { onMessage: _onMessage });
});
onUnmounted(() => _unsubscribe?.());
</script>

<template>
  <n-card title="手动下单" style="margin-bottom: 16px">
    <n-alert v-if="error" type="error" :title="error" style="margin-bottom: 12px" />
    <n-form label-placement="left" label-width="110">
      <n-form-item label="合约">
        <n-input v-model:value="form.contract" placeholder="rb2701" />
      </n-form-item>
      <n-form-item label="方向">
        <n-select v-model:value="form.direction" :options="directionOptions" />
      </n-form-item>
      <n-form-item label="开平">
        <n-select v-model:value="form.offset" :options="offsetOptions" />
      </n-form-item>
      <n-form-item label="手数">
        <n-input-number v-model:value="form.quantity" :min="1" :max="1000" />
      </n-form-item>
      <n-form-item label="委托类型">
        <n-select v-model:value="form.price_type" :options="priceTypeOptions" />
      </n-form-item>
      <n-form-item v-if="form.price_type === 'LIMIT'" label="限价">
        <n-input-number v-model:value="form.limit_price" :min="0" :step="1" />
      </n-form-item>
      <n-form-item>
        <n-space align="center">
          <n-button type="primary" :loading="submitting" @click="submit">下单</n-button>
          <span class="dim">人工优先：只过基础校验，不经策略风控</span>
        </n-space>
      </n-form-item>
    </n-form>
    <n-alert v-if="lastAccepted" type="info" :title="'已投递：' + String(lastAccepted.contract ?? '')"
             style="margin-bottom: 12px">
      {{ lastAccepted.note }}
    </n-alert>
  </n-card>

  <n-card title="执行结果（实时 trade.order.status）">
    <n-space vertical>
      <n-space v-for="(ev, i) in events" :key="i" align="center" justify="space-between">
        <n-space align="center">
          <n-tag :type="ev.status === 'FILLED' ? 'success' : ev.status === 'ERROR' ? 'error' : 'warning'" size="small">
            {{ ev.status }}
          </n-tag>
          <b>{{ ev.contract }}</b>
          <span class="dim">成交 {{ ev.filled ?? "-" }}/{{ ev.requested ?? "-" }}</span>
          <span class="dim" v-if="ev.avg_price">均价 {{ ev.avg_price }}</span>
          <span class="dim" v-if="ev.attempts">追单 {{ ev.attempts }} 轮</span>
          <span class="dim" v-if="ev.msg">{{ ev.msg }}</span>
        </n-space>
        <span class="dim">{{ ev.instance_id }}</span>
      </n-space>
      <n-empty v-if="events.length === 0" description="暂无执行回报" />
    </n-space>
  </n-card>
</template>

<style scoped>
.dim {
  color: #888;
  font-size: 12px;
}
</style>
