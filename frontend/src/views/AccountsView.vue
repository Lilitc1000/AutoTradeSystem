<script setup lang="ts">
/** 账号管理：CRUD + 连接测试 + 激活（M3-e1）。 */
import { onMounted, reactive, ref } from "vue";
import { NAlert, NButton, NCard, NForm, NFormItem, NInput, NSpace, NSpin, NTag } from "naive-ui";
import { accountsApi, type AccountPayload, type TradeAccountView } from "../api/accounts";

const accounts = ref<TradeAccountView[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const testing = ref<number | null>(null); // 正在测试的账号 id
const showForm = ref(false);
const editingId = ref<number | null>(null);

const form = reactive<AccountPayload>({
  name: "",
  broker_id: "9999",
  user_id: "",
  password: "",
  auth_code: "",
  app_id: "simnow_client_test",
  md_front: "tcp://182.254.243.31:30011",
  trade_front: "tcp://182.254.243.31:30001",
});

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    accounts.value = (await accountsApi.list()).accounts;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editingId.value = null;
  Object.assign(form, {
    name: "", broker_id: "9999", user_id: "", password: "",
    auth_code: "", app_id: "simnow_client_test",
    md_front: "tcp://182.254.243.31:30011", trade_front: "tcp://182.254.243.31:30001",
  });
  showForm.value = true;
}

function openEdit(row: TradeAccountView) {
  editingId.value = row.id;
  Object.assign(form, { ...row, password: "", auth_code: "" });
  showForm.value = true;
}

async function save() {
  error.value = null;
  try {
    if (editingId.value !== null) {
      await accountsApi.update(editingId.value, form);
    } else {
      await accountsApi.create(form);
    }
    showForm.value = false;
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function activate(row: TradeAccountView) {
  error.value = null;
  try {
    await accountsApi.activate(row.id);
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function remove(row: TradeAccountView) {
  error.value = null;
  try {
    await accountsApi.remove(row.id);
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function test(row: TradeAccountView) {
  error.value = null;
  testing.value = row.id;
  try {
    await accountsApi.test(row.id);
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    testing.value = null;
  }
}

onMounted(refresh);
</script>

<template>
  <n-spin :show="loading">
    <n-alert v-if="error" type="error" :title="error" style="margin-bottom: 12px" />

    <n-card title="交易账号" style="margin-bottom: 16px">
      <template #header-extra>
        <n-button type="primary" size="small" @click="openCreate">添加账号</n-button>
      </template>
      <n-space vertical>
        <n-card v-for="row in accounts" :key="row.id" size="small" :bordered="true">
          <n-space justify="space-between" align="center">
            <n-space vertical size="small">
              <n-space align="center">
                <n-tag :type="row.is_active ? 'success' : 'default'" size="small">
                  {{ row.is_active ? "使用中" : "未激活" }}
                </n-tag>
                <b>{{ row.name }}</b>
                <span class="dim">{{ row.broker_id }} / {{ row.user_id }}</span>
              </n-space>
              <span class="dim">{{ row.trade_front }}</span>
              <n-space align="center" v-if="row.last_test_ok !== null">
                <n-tag :type="row.last_test_ok ? 'success' : 'error'" size="small">
                  {{ row.last_test_ok ? "连接测试通过" : "连接测试失败" }}
                </n-tag>
                <span class="dim">{{ row.last_test_msg }}</span>
              </n-space>
            </n-space>
            <n-space>
              <n-button size="small" :loading="testing === row.id" @click="test(row)">连接测试</n-button>
              <span class="dim" v-if="row.last_test_ok === false">上次失败：CTP 会话释放中，约 1 分钟后再试</span>
              <n-button v-if="!row.is_active" size="small" type="primary" ghost @click="activate(row)">设为使用</n-button>
              <n-button size="small" @click="openEdit(row)">编辑</n-button>
              <n-button size="small" type="error" ghost @click="remove(row)">删除</n-button>
            </n-space>
          </n-space>
        </n-card>
        <n-empty v-if="accounts.length === 0" description="暂无账号——添加 SimNow 账号开始" />
      </n-space>
    </n-card>

    <n-card v-if="showForm" :title="editingId !== null ? '编辑账号' : '添加账号'">
      <n-form label-placement="left" label-width="110">
        <n-form-item label="名称">
          <n-input v-model:value="form.name" placeholder="如：SimNow 主账号" />
        </n-form-item>
        <n-form-item label="BrokerID">
          <n-input v-model:value="form.broker_id" placeholder="9999" />
        </n-form-item>
        <n-form-item label="投资者账号">
          <n-input v-model:value="form.user_id" placeholder="SimNow user_id" />
        </n-form-item>
        <n-form-item label="交易密码">
          <n-input v-model:value="form.password" type="password" show-password-on="click"
                   :placeholder="editingId !== null ? '留空则不修改' : '交易密码'" />
        </n-form-item>
        <n-form-item label="AuthCode">
          <n-input v-model:value="form.auth_code" placeholder="看穿式监管 auth code（可空）" />
        </n-form-item>
        <n-form-item label="AppID">
          <n-input v-model:value="form.app_id" />
        </n-form-item>
        <n-form-item label="行情前置">
          <n-input v-model:value="form.md_front" placeholder="tcp://..." />
        </n-form-item>
        <n-form-item label="交易前置">
          <n-input v-model:value="form.trade_front" placeholder="tcp://..." />
        </n-form-item>
        <n-form-item>
          <n-space>
            <n-button type="primary" @click="save">保存（密码加密存储）</n-button>
            <n-button @click="showForm = false">取消</n-button>
          </n-space>
        </n-form-item>
      </n-form>
    </n-card>
  </n-spin>
</template>

<style scoped>
.dim {
  color: #888;
  font-size: 12px;
}
</style>
