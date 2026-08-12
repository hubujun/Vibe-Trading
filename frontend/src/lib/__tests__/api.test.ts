import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";

async function loadApiModule() {
  vi.resetModules();
  return import("../api");
}

describe("api request helper", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => ""),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("rejects non-JSON responses with a descriptive error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<!doctype html><html><body>SPA</body></html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
      ),
    );

    const { api } = await loadApiModule();

    await expect(api.getChannelStatus()).rejects.toMatchObject({
      name: "ApiError",
      status: 200,
      message: expect.stringContaining("Expected JSON from /channels/status, got text/html"),
    } satisfies Partial<ApiError>);
  });

  it("posts an authenticated connector verification request with an encoded profile id", async () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => "remote-test-key"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", connection_state: "connected" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await loadApiModule();

    await expect(api.verifyConnector("longbridge/live sdk")).resolves.toMatchObject({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/live/connectors/longbridge%2Flive%20sdk/verify?force=true",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer remote-test-key" }),
      }),
    );
  });

  it("sends the stored API key when fetching a correlation matrix", async () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => "remote-test-key"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ labels: ["A", "B"], matrix: [[1, 0], [0, 1]] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await loadApiModule();

    await expect(api.getCorrelation("A,B", 90, "pearson")).resolves.toEqual({
      labels: ["A", "B"],
      matrix: [[1, 0], [0, 1]],
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/correlation?codes=A%2CB&days=90&method=pearson",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer remote-test-key" }),
      }),
    );
  });

  it("sends the stored API key when fetching a correlation regime timeline", async () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => "remote-test-key"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    const regime = {
      labels: ["A", "B"],
      dates: ["2024-01-01"],
      density: [0.5],
      smoothed: [0.5],
      fused: [0],
      episodes: [],
      params: {},
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(regime), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await loadApiModule();

    await expect(api.getCorrelationRegime("A,B", 90)).resolves.toEqual(regime);
    expect(fetchMock).toHaveBeenCalledWith(
      "/correlation/regime?codes=A%2CB&days=90",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer remote-test-key" }),
      }),
    );
  });

  it("serializes options payoff params to snake_case query args", async () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => "remote-test-key"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    const payoff = {
      strategy: "bull_call_spread",
      entry_spot: 100,
      time_to_expiry: 0.25,
      rate: 0.05,
      iv: 0.3,
      multiplier: 100,
      net_premium: 450.5,
      entry_commission: 0.45,
      entry_cost: 450.95,
      breakevens: [105.5],
      max_profit: 545.05,
      max_loss: -450.95,
      profit_unbounded: false,
      loss_unbounded: false,
      curve: [{ spot: 50, pnl: -450.95 }],
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payoff), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await loadApiModule();

    await expect(
      api.getOptionsPayoff({
        strategy: "bull_call_spread",
        lowerStrike: 95,
        upperStrike: 105,
        qty: 2,
        entrySpot: 100,
        multiplier: 100,
      }),
    ).resolves.toEqual(payoff);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/options-lab/payoff?strategy=bull_call_spread&lower_strike=95&upper_strike=105&qty=2&entry_spot=100&multiplier=100",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer remote-test-key" }),
      }),
    );
  });

  it("omits undefined options payoff params from the query string", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ strategy: "long_straddle", curve: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await loadApiModule();

    await api.getOptionsPayoff({ strategy: "long_straddle", strike: 100 });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/options-lab/payoff?strategy=long_straddle&strike=100",
      expect.anything(),
    );
  });

  it("resolves authorization errors through the active i18n key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "server fallback" }), {
          status: 401,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const apiModule = await loadApiModule();
    const { default: i18n } = await import("@/i18n");
    i18n.addResource(
      "en",
      "translation",
      "agent.authRequired",
      "Add an API key in Settings.",
    );
    await i18n.changeLanguage("en");

    expect(apiModule.AUTH_REQUIRED_MESSAGE).toBe("Add an API key in Settings.");
    await expect(apiModule.api.getCorrelation("A,B", 90, "pearson")).rejects.toMatchObject({
      status: 401,
      message: "Add an API key in Settings.",
    } satisfies Partial<ApiError>);
  });
});
