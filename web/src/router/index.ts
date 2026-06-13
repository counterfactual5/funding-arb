import { createRouter, createWebHistory } from "vue-router";
import Scanner from "@/views/Scanner.vue";
// Other views are lazy-loaded to reduce initial bundle size
// (ethers + @nktkas/hyperliquid only load when DEX page is visited)

const routes = [
  {
    path: "/",
    name: "Scanner",
    component: Scanner,
    meta: { titleKey: "menu.scanner" },
  },
  {
    path: "/positions",
    name: "Positions",
    component: () => import("@/views/Positions.vue"),
    meta: { titleKey: "menu.positions" },
  },
  {
    path: "/backtest",
    name: "Backtest",
    component: () => import("@/views/Backtest.vue"),
    meta: { titleKey: "menu.backtest" },
  },
  {
    path: "/cex",
    name: "CexConnection",
    component: () => import("@/views/CexConnection.vue"),
    meta: { titleKey: "menu.cex" },
  },
  {
    path: "/dex",
    name: "DexConnection",
    component: () => import("@/views/DexConnection.vue"),
    meta: { titleKey: "menu.dex" },
  },
  {
    path: "/strategy",
    name: "StrategySettings",
    component: () => import("@/views/StrategySettings.vue"),
    meta: { titleKey: "menu.strategy" },
  },
  {
    path: "/fees",
    name: "FeeSettings",
    component: () => import("@/views/FeeSettings.vue"),
    meta: { titleKey: "menu.fees" },
  },
  {
    path: "/advanced",
    name: "AdvancedSettings",
    component: () => import("@/views/AdvancedSettings.vue"),
    meta: { titleKey: "menu.advanced" },
  },
  {
    path: "/settings",
    redirect: "/cex",
  },
  {
    path: "/docs",
    redirect: "/docs/readme",
  },
  {
    path: "/docs/:slug",
    name: "Docs",
    component: () => import("@/views/Docs.vue"),
    meta: { titleKey: "menu.docs" },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(() => {
  document.title = "Funding Arb";
});

export default router;
