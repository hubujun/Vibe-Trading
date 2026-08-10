# Identity Resolution and Symbol Reuse Guidance

## Key rule

After `search_symbol` has locked a canonical identity, always reuse that exact locked symbol and venue for any subsequent market-sensitive tool call in the same session.

## Why this matters

The agent’s grounding system enforces that market consumers only use identities that were locked before the current assistant tool-call batch. If a `get_market_data` or other market-sensitive call uses a different symbol spelling or venue alias, it will be rejected as `identity_mismatch` and force an extra resolver cycle.

## Practical guidance

- If the user names a company, fund, or asset without a canonical venue suffix, call `search_symbol` first.
- Wait for `search_symbol` to succeed before invoking `get_market_data` or related market tools.
- Do not retry `get_market_data` with a different spelling after resolution; use the locked symbol exactly.
- Equivalent spellings like `BTC-USDT` / `BTC/USDT` or `.SS` / `.SH` may normalize to the same identity, but the next market tool must still consume the locked canonical form from the resolver result.
- Do not silently change a listed security into a private-company workflow.

## Example flow

1. User asks: “What's the latest price for TANTALUS SYSTEMS HOLDINGS INC?”
2. Agent calls `search_symbol(query="TANTALUS SYSTEMS HOLDINGS INC")`
3. Resolver locks `GRID.TO`
4. Agent calls `get_market_data(codes=["GRID.TO"])`

If the agent instead calls `get_market_data(codes=["GRID"])` or `get_market_data(codes=["GRID.TO.V"])` after the resolver, the grounding layer will reject it and extra latency will occur.

## When to ask the user

If the resolver returns multiple valid candidates or ambiguous listings, present the options and ask the user to choose one. Do not guess.
