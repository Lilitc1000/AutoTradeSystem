<script setup lang="ts">
/** 应用外壳：顶部导航（总览 / 交易监控）。 */
import { NConfigProvider, NMessageProvider } from "naive-ui";
</script>

<template>
  <n-config-provider>
    <n-message-provider>
      <div class="shell">
        <header class="topbar">
          <span class="brand">ATS 交易工作台</span>
          <nav>
            <router-link to="/dashboard">总览</router-link>
            <router-link to="/trading">交易监控</router-link>
            <router-link to="/accounts">交易账号</router-link>
          </nav>
        </header>
        <main class="content">
          <router-view />
        </main>
      </div>
    </n-message-provider>
  </n-config-provider>
</template>

<style>
.shell {
  min-height: 100vh;
  background: var(--color-bg, #f5f5f5);
}
.topbar {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 10px 20px;
  background: #fff;
  border-bottom: 1px solid #e5e5e5;
}
.brand {
  font-weight: 600;
}
.topbar nav a {
  margin-right: 16px;
  color: #333;
  text-decoration: none;
}
.topbar nav a.router-link-active {
  color: #18a058;
  font-weight: 600;
}
.content {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}
</style>
