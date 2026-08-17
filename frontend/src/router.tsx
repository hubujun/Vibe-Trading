import { Suspense, lazy, type ComponentType } from "react";
import { createBrowserRouter, Navigate } from "react-router";
import { Layout } from "@/components/layout/Layout";

const Home = lazy(() => import("@/pages/Home").then((m) => ({ default: m.Home })));
const Agent = lazy(() => import("@/pages/Agent").then((m) => ({ default: m.Agent })));
const RunDetail = lazy(() =>
  import("@/pages/RunDetail").then((m) => ({ default: m.RunDetail })),
);
const Compare = lazy(() =>
  import("@/pages/Compare").then((m) => ({ default: m.Compare })),
);
const Settings = lazy(() =>
  import("@/pages/Settings").then((m) => ({ default: m.Settings })),
);
const Runtime = lazy(() =>
  import("@/pages/Runtime").then((m) => ({ default: m.Runtime })),
);
// /combo 与 /autopilot 已合并进 /workbench (策略流水线工作台), 路由保留重定向.
const Workbench = lazy(() =>
  import("@/pages/Workbench").then((m) => ({ default: m.Workbench })),
);
const Scheduled = lazy(() =>
  import("@/pages/Scheduled").then((m) => ({ default: m.Scheduled })),
);
const Reports = lazy(() =>
  import("@/pages/Reports").then((m) => ({ default: m.Reports })),
);
const Portfolio = lazy(() =>
  import("@/pages/Portfolio").then((m) => ({ default: m.Portfolio })),
);
const Correlation = lazy(() =>
  import("@/pages/Correlation").then((m) => ({ default: m.Correlation })),
);
const AlphaZoo = lazy(() =>
  import("@/pages/AlphaZoo").then((m) => ({ default: m.AlphaZoo })),
);
const OptionsLab = lazy(() =>
  import("@/pages/OptionsLab").then((m) => ({ default: m.OptionsLab })),
);
const PortfolioStudio = lazy(() =>
  import("@/pages/PortfolioStudio").then((m) => ({ default: m.PortfolioStudio })),
);

function PageLoader() {
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}

function wrap(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <Component />
    </Suspense>
  );
}

export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: wrap(Agent) },
      { path: "/about", element: wrap(Home) },
      { path: "/agent", element: wrap(Agent) },
      { path: "/runtime", element: wrap(Runtime) },
      { path: "/autopilot", element: <Navigate to="/workbench" replace /> },
      { path: "/scheduled", element: wrap(Scheduled) },
      { path: "/reports", element: wrap(Reports) },
      { path: "/portfolio", element: wrap(Portfolio) },
      { path: "/settings", element: wrap(Settings) },
      { path: "/runs/:runId", element: wrap(RunDetail) },
      { path: "/compare", element: wrap(Compare) },
      { path: "/correlation", element: wrap(Correlation) },
      { path: "/options", element: wrap(OptionsLab) },
      { path: "/alpha-zoo", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/bench", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/compare", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/:alphaId", element: wrap(AlphaZoo) },
      { path: "/options-lab", element: wrap(OptionsLab) },
      { path: "/portfolio-studio", element: wrap(PortfolioStudio) },
      { path: "/combo", element: <Navigate to="/workbench" replace /> },
      { path: "/autopilot", element: <Navigate to="/workbench" replace /> },
      { path: "/workbench", element: wrap(Workbench) },
    ],
  },
]);
