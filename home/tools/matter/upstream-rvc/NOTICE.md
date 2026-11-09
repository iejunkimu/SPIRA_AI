These JavaScript files are TypeScript-transpiled copies of the upstream matter.js
v0.18.0 RVC example (Apache-2.0), commit
7e1f5d7892dcea4330828b6a82d31ffe2a04cc0b:
https://github.com/matter-js/matter.js/tree/v0.18.0/examples/device-robotic-vacuum-cleaner/src

Generated with TypeScript 5.9.3, ES2022 target and module. Changes: expose the
endpoint factory as addRvc(node), omit standalone node startup, allow explicitly
labelled simulation round/docking durations through SPIRA_RVC_ROUND_SECONDS
(default 600) and SPIRA_RVC_DOCK_SECONDS (default 30). All state transitions,
protocol handlers, service-area progress and completion events remain upstream.
No Home command identity is inserted into this device or its protocol events.
