# TODO / Gap Analysis and Plan

This file tracks gaps vs. the upstream Go session-manager-plugin and a step-by-step plan to close high‑value items. After each implemented item, pause and verify behavior manually, then check it off here before proceeding to the next.

## Gaps vs. Go Implementation

- Protocol: Include `ClientId` and `ClientVersion` in initial handshake text frame (FinalizeDataChannelHandshake).
- Handshake actions: Properly handle `HandshakeRequest` actions. Today we minimally respond; we don’t set session type or support KMS.
- Flow control: Honor `start_publication` and `pause_publication` to gate input sending.
- Terminal: Periodic terminal size updates (Go sends every 500ms) in addition to SIGWINCH.
- Signals: Forward additional control signals (SIGQUIT, SIGTSTP) with correct bytes.
- Sequencing buffers (optional): Buffer out-of-order output messages and process in order (Go does this); add outgoing resend buffer. Not required immediately.
- Port/Interactive sessions (optional): Stubs exist; not in current scope.

## High‑Value Steps (Implement in order)

1. Add `ClientId` and `ClientVersion` to handshake
   - Update data channel to accept client info and include it in the initial text handshake JSON.
   - Wire the CLI to pass the generated client id into the data channel before it opens.
   - Verification: Start a session; confirm handshake still succeeds and interactivity works (type `echo ok`).
   - Status: DONE (verified)

2. Honor start/pause publication for input
   - Handle `start_publication` / `pause_publication` message types; toggle an `input_allowed` flag; skip sending input when paused.
   - Verification: Session should remain interactive; if pause messages occur, input should temporarily stop (hard to simulate; basic regression is sufficient).
   - Status: DONE (verified)

3. Periodic resize heartbeat
   - Add a 500ms loop that sends terminal size while connected (as Go does), in addition to on SIGWINCH.
   - Verification: Resize the terminal; prompt should reflow as expected; no errors.

4. Forward SIGQUIT and SIGTSTP
   - Map SIGQUIT to `\x1c` and SIGTSTP to `\x1a` (Unix); forward like SIGINT.
   - Verification: Pressing Ctrl-\ or Ctrl-Z should be forwarded to the remote (behavior may depend on shell/remote policy).
   - Terminal config note: also disabled `ISIG` in cbreak mode so the local terminal does not handle Ctrl-C/Z/\.
   - Status: DONE (verified)

5. Improve channel_closed message
   - Include SessionId in the printed friendly message (use payload SessionId field when present).
   - Verification: Run `exit`; message should read `SessionId: <id> : <Output>` or `Exiting session with sessionId: <id>.`
   - Status: DONE (verified)

6. Handle SessionType from HandshakeRequest
   - Parse `RequestedClientActions` where `ActionType == SessionType`, store session type and properties, and include in diagnostics.
   - Verification: Start a session with `-v` and confirm a log like `Handshake: session_type=<value>` appears after handshake.
   - Status: DONE (verified by functionality; log may be absent if agent omitted action)

7. Input coalescing (optional)
   - Coalesce keystrokes into short bursts (≈10ms) or until CR/size threshold. Control bytes (Ctrl-C/Z/\) flush immediately. Disabled by default; enabled automatically only for non‑TTY stdin to avoid interactive lag.
   - Verification: With normal TTY sessions, interactivity remains snappy (no lag). With piped input, debug logs show fewer input messages.
   - Status: DONE (verified)

8. Out-of-order output buffering (optional)
   - Buffer future `output_stream_data` frames by sequence and print them in order starting from the expected sequence; still ack on receipt.
   - Verification: No functional change under normal conditions. Under out-of-order delivery, output should appear correctly ordered.
   - Status: DONE (verified)
 

## Process / Instructions

- After each step is implemented, the assistant will pause and ask you to verify. Once you confirm it works, we will check off the item here and proceed to the next.
- If verification fails, we will iterate on that step until it passes before moving on.

## Code Simplification & Idiomatic Improvements

Proposed improvements to reduce complexity and make the code more idiomatic:

1) Centralize message names and payload types
   - Add message name constants (e.g., `input_stream_data`, `output_stream_data`, `acknowledge`, `channel_closed`, `start_publication`, `pause_publication`) and convert `PayloadType` to `IntEnum`.
   - Replace magic strings and raw ints across code.
   - Verification: Normal session (connect, type, exit) still works.

2) Unify message serialization helpers
   - Merge `serialize_client_message` and `serialize_client_message_with_payload_type` into a single function with an optional `payload_type` parameter.
   - Update call sites accordingly.
   - Verification: Acks and input still accepted (no seq 0 loops).

3) Trim unused dependencies
   - Remove `pydantic` from `pyproject.toml` (present but unused) to reduce footprint.
   - Verification: `uv run` still installs/executes; all tests pass.

4) CLI flag for input coalescing
   - Add `--coalesce-input/--no-coalesce-input` and optional `--coalesce-delay-ms`.
   - Respect TTY default (off) vs. non‑TTY (on) unless explicitly overridden.
   - Verification: Flag toggles behavior; no lag when disabled.

5) Async signal handling on Unix
   - Prefer `loop.add_signal_handler` where available; fall back to `signal.signal` otherwise.
   - Verification: Ctrl-C/Z/\ forwarding still works; process exits cleanly on SIGTERM.

6) Constants module / small refactors
   - Move protocol constants (payload types, message names, client version) to a dedicated module; keep data channel slimmer via smaller helpers.
   - Status: DONE (verified)

7) Typing modernization
   - Use `| None` and `dict[str, Any]` style hints; add return types for helpers.
   - Status: DONE (verified)

8) Tests coverage additions
   - Add unit tests for ack UUID layout/digest and out-of-order buffering.
   - Status: PARTIAL (ack format + out-of-order buffering added)

9) Logging tuning
   - Normalize log levels; add a concise handshake summary (agent_version, session_type, client_version) and keep per-frame details at DEBUG.
   - Status: DONE (verified)

10) Optional outgoing resend buffer
   - Only if needed; otherwise omit for simplicity with WebSocket reliability.

## Next Up (Implement in this order)

- [x] 1) Centralize message names and payload types
- [x] 2) Unify message serialization helpers
- [x] 3) Trim unused dependencies (`pydantic`)
- [x] 4) CLI flag for input coalescing
- [x] 5) Async signal handling on Unix
