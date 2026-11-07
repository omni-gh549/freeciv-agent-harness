# Testing

## Local and CI regression suite

```sh
python -m pip install -e '.[test]'
python -m unittest discover -s tests -v
```

The suite covers partial state merges, ownership and fog-of-war boundaries, stale/duplicate/concurrent end-turn calls, unknown tool arguments, injection-resistant game commands, event pagination/replay/truncation/long-polling, HTTP authentication/origin rejection, unconfirmed actions, runner plan validation and subprocess execution, and real stdio interoperability with the **official MCP Python client SDK**.

CI runs the same suite on Ubuntu and Windows with Python 3.11 and 3.13. A local Windows run on Python 3.14 is also supported and tested. Live Docker tests are explicit, separate tests; normal unit/CI tests never start or connect to a game.

## Isolated live test

Build the compatible Freeciv-web image separately. Then create a disposable instance with only a random loopback HTTP port exposed:

```sh
docker run -d --name freeciv-harness-test \
  --label freeciv-agent-harness.test=true \
  -p 127.0.0.1::80 freeciv/freeciv-web sleep infinity
docker port freeciv-harness-test 80/tcp
```

Wait for `docker logs freeciv-harness-test` to show the stack ready. Use its actual mapped port:

```sh
python tools/integration_smoke.py --url http://127.0.0.1:<port> --container freeciv-harness-test
```

The script refuses a container without the explicit disposable-test label and refuses a URL that does not match that container's published port. It refuses to replace an already-running game. It changes lobby settings, creates a new test game, founds a city, selects production/research, moves units, sends chat to an independent peer, advances a turn, saves, and rejoins. No browser is involved. It does not inspect or alter another container.

For a repeat run, restart the disposable container to return to its fresh lobby; re-check the port because Docker may choose a new one. Stop/remove only that test container afterward:

```sh
docker stop freeciv-harness-test
docker rm freeciv-harness-test
```

## Validation record

Tested 7 November 2026 against Freeciv-web checkout revision `c19ce060fadc99663f8aba3652ca94b07174467c`, Freeciv.Web.Devel-3.3 JSON protocol, and a locally built `freeciv/freeciv-web` image.

- 50 local regression/interoperability tests passed.
- 28 checks passed in a fresh, isolated live Docker game, including real pathfinding/goto, server action queries, unit activities, city buildability on rejoin, tax rates, save-file existence, independent chat receipt, next-turn events, nation/lobby configuration and the standalone agent runner completing a turn.
- Native gameplay tests used the Python service; the official MCP SDK separately initialized, discovered tools, called status, and received typed tool errors through a real subprocess stdio transport.
- The CLI/HTTP transport separately connected, sent a real in-game message, retrieved its native event, and shut down cleanly. The MCP server has no connection or gameplay side effects at initialization.
- Hosting stop/backup/start/import was checked separately using the disposable game's save. Restore leaves the loaded game in the pregame lobby; city/unit details arrive when it is resumed.

The browser is never part of these tests. The test extra adds the official MCP SDK only for interoperability; the installed harness itself requires just `websockets` beyond the standard library.

Government changes, rush buying and every individual combat/transport/terrain action have not each been exercised against a live ruleset. Their tools expose the native request path and return server processing/state confirmation plus messages. Servers and forks outside the recorded protocol profile are not covered. The passive example agent validates transport behavior, not strategic playing ability.
