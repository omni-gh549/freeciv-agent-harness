# Architecture

```text
Agent (any MCP client)                 CLI / scripts
       | stdio JSON-RPC                      | local HTTP + bearer token
       v                                     v
   MCP adapter                         optional API daemon
       \                                     /
        +---------- GameService ------------+
                         |
                  typed owned actions
                         |
                     GameClient
                   /             \
             GameState          EventLog
                   \             /
                native WebSocket JSON
                         |
          Freeciv-web /civsocket/<game-port+1000>
                         |
                 Freeciv game server
```

MCP and the API daemon are alternative owners of a connection. Do not connect two sessions under the same player name unless you intend to replace/reconnect the client. Neither layer needs a browser session or a message queue in an assistant product.

The receiver thread handles server pings and merges partial packet updates into state tables. It emits events at server begin-turn boundaries, incoming chat/messages, connection changes, and action results. The event journal is optional and stores game content, never login credentials. Reads return a deep copy. Public player metadata excludes opponents' economies; map queries omit unknown tiles; city/unit APIs return only the controlled player's entities.

Each service serializes mutation validation and execution under one lock. `end_turn` checks the observed turn and the latest server phase_done flag. The sender never retries mutations automatically. Expected-state confirmation is used where possible; otherwise processing acknowledgement and server messages are returned separately, without a blanket success claim.

The profile is generated from upstream `common/networking/packets.def`. Freeciv's web proxy already expands the binary game's packets to JSON. The harness handles those JSON messages rather than reproducing the desktop client's binary codec or implementing a game engine.

This implementation deliberately leaves model selection, reasoning loops and idle-chat wake-up to the caller. MCP provides explicit tools; `freeciv_events` supports long-polling up to 55 seconds. A native host can consume the event stream and invoke its agent directly. There is no hidden scheduler, browser hook, or Codex-specific dependency.
