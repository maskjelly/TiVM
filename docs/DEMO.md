# TiVM product walkthrough

## Prepare

Build and boot the local desktop once. The first image build downloads the desktop, browser, and
toolchain and can take several minutes.

```sh
cp .env.example .env
# Set OPENAI_API_KEY in .env. TiVM executes generated shell commands inside its container.
make vm       # macOS only; Linux uses the existing Docker daemon
make up
```

Open [the control panel](http://localhost:6081). It binds to localhost by default.

## Show the flow

1. Point out the live desktop and the accessibility-first element list.
2. Start the example contract suite from the panel, or submit it through the API:

   ```sh
   curl -fsS -X POST http://localhost:6081/api/run \
     -H 'Content-Type: application/json' \
     -d '{"app":{"repo":"maskjelly/TiVM","ref":"main"}}'
   ```

3. Follow a user journey in Firefox. Show the current plan, selected UI element, action, and
   completion check in the timeline.
4. Open the finished run report. Review the per-flow verdict, final screenshot, video, and step
   history.
5. Start the same contract suite again to show trace replay. The recorded demo replay completed the
   included todo flows with zero planner tokens; runtime and replay success depend on the app and
   environment.

## Frame the result accurately

TiVM is a pre-1.0 prototype. Existing hosted evidence covers the included todo app and its two
contract flows. It does not establish general pass rates, false-pass rates, cross-framework
coverage, or production-grade security. Use a trusted repository during the demo. Public execution
of untrusted pull requests remains gated on credential and execution isolation.
