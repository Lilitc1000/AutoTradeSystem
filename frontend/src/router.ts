import { createRouter, createWebHistory } from "vue-router";

const routes = [
  { path: "/", redirect: "/dashboard" },
  { path: "/dashboard", name: "dashboard", component: () => import("./views/DashboardView.vue") },
  { path: "/trading", name: "trading", component: () => import("./views/TradingView.vue") },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
