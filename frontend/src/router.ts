import { createRouter, createWebHistory } from "vue-router";

const routes = [
  { path: "/", redirect: "/dashboard" },
  { path: "/dashboard", name: "dashboard", component: () => import("./views/DashboardView.vue") },
  { path: "/trading", name: "trading", component: () => import("./views/TradingView.vue") },
  { path: "/accounts", name: "accounts", component: () => import("./views/AccountsView.vue") },
  { path: "/manual", name: "manual", component: () => import("./views/ManualTradeView.vue") },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
