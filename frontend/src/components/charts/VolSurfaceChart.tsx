import { useEffect, useRef } from "react";
import i18n from "@/i18n";
import type { OptionsSurface, OptionsSurfaceExpiry } from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { echarts, CHART_GROUP, connectCharts } from "@/lib/echarts";
import { useThemeDark } from "@/lib/theme-store";

interface Props {
  surface: OptionsSurface;
  height?: number;
}

interface LinePoint {
  strike: number;
  iv: number;
}

function curve(expiry: OptionsSurfaceExpiry, side: "call" | "put"): LinePoint[] {
  return expiry.contracts
    .map((row) => ({ strike: row.strike, iv: side === "call" ? row.iv_call : row.iv_put }))
    .filter((p): p is LinePoint => p.iv != null && Number.isFinite(p.iv))
    .sort((a, b) => a.strike - b.strike);
}

/** Implied-volatility surface: per-expiry call/put IV curves vs strike. */
export function VolSurfaceChart({ surface, height = 320 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const dark = useThemeDark();

  useEffect(() => {
    if (!ref.current || surface.expirations.length === 0) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);
    chart.group = CHART_GROUP;
    connectCharts();

    const series = surface.expirations.flatMap((expiry, index) => {
      const calls = curve(expiry, "call");
      const puts = curve(expiry, "put");
      const color = index % 2 === 0 ? t.infoColor : t.warningColor;
      return [
        {
          name: `${expiry.expiration}-C`,
          type: "line" as const,
          data: calls.map((p) => [p.strike, Number((p.iv * 100).toFixed(2))]),
          smooth: true,
          symbol: "none",
          lineStyle: { color, width: 2, type: "solid" as const },
        },
        {
          name: `${expiry.expiration}-P`,
          type: "line" as const,
          data: puts.map((p) => [p.strike, Number((p.iv * 100).toFixed(2))]),
          smooth: true,
          symbol: "none",
          lineStyle: { color, width: 2, type: "dashed" as const },
        },
      ];
    });

    chart.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: t.tooltipBg,
        borderColor: t.tooltipBorder,
        textStyle: { color: t.tooltipText, fontSize: 11 },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (params: any) => {
          if (!Array.isArray(params) || !params.length) return "";
          const strike = params[0].value[0];
          let html = `<b>${i18n.t("optionsLab.colStrike")} ${strike}</b>`;
          for (const p of params) {
            html += `<br/>${p.marker} ${p.seriesName}: <b>${p.value[1]}%</b>`;
          }
          return html;
        },
      },
      legend: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (name: string) => {
          const [exp, side] = name.split("-");
          const sideName = side === "C" ? i18n.t("optionsLab.call") : i18n.t("optionsLab.put");
          return `${new Date(Number(exp) * 1000).toLocaleDateString()} ${sideName}`;
        },
        textStyle: { color: t.textColor, fontSize: 11 },
        top: 4,
        type: "scroll",
      },
      grid: { left: 8, right: 16, top: 40, bottom: 8, containLabel: true },
      xAxis: {
        type: "value",
        name: i18n.t("optionsLab.colStrike"),
        nameTextStyle: { color: t.textColor, fontSize: 10 },
        axisLine: { lineStyle: { color: t.axisColor } },
        axisLabel: { color: t.textColor, fontSize: 10 },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        name: "IV %",
        nameTextStyle: { color: t.textColor, fontSize: 10 },
        splitLine: { lineStyle: { color: t.gridColor } },
        axisLabel: { color: t.textColor, fontSize: 10, formatter: "{value}%" },
      },
      series,
    });

    let resizeFrame: number | null = null;
    const ro = new ResizeObserver(() => {
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = null;
        chart.resize();
      });
    });
    ro.observe(ref.current!);
    return () => {
      ro.disconnect();
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
      chart.dispose();
    };
  }, [surface, dark]);

  if (surface.expirations.length === 0) {
    return (
      <div className="text-sm text-muted-foreground p-4">
        {i18n.t("optionsLab.surfaceEmpty")}
      </div>
    );
  }
  return <div ref={ref} style={{ height }} />;
}
