import { createRouter, createWebHistory } from "vue-router";
import Scanner from "@/views/Scanner.vue";
import Positions from "@/views/Positions.vue";
import Backtest from "@/views/Backtest.vue";
import Settings from "@/views/Settings.vue";
import Docs from "@/views/Docs.vue";

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
    component: Positions,
    meta: { titleKey: "menu.positions" },
  },
  {
    path: "/backtest",
    name: "Backtest",
    component: Backtest,
    meta: { titleKey: "menu.backtest" },
  },
  {
    path: "/settings",
    name: "Settings",
    component: Settings,
    meta: { titleKey: "menu.settings" },
  },
  {
    path: "/docs",
    redirect: "/docs/cross-interval",
  },
  {
    path: "/docs/:slug",
    name: "Docs",
    component: Docs,
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
