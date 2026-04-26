# Polymarket API Notes

- Gamma API is public and is used for market discovery.
- CLOB API public endpoints are used for order books, midpoints, spreads, and price history.
- Market WebSocket endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
- Subscribe with `{"type":"market","assets_ids":["<token_id>"],"custom_feature_enabled":true}`.
- Trading endpoints require authentication, but v1 is paper-only and must not call order-placement methods.
