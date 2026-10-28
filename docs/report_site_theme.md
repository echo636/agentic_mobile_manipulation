# Shared report appearance

Run `python -m manipulation_agent.report_server --directory <reports> --port 8765`
with the project package on `PYTHONPATH`. The workspace's `scripts/serve_reports.py`
is a compatibility entry point for this server.

Every served HTML report receives the same appearance control: light, dark, or
system preference. The choice persists in browser local storage and synchronizes
across same-origin tabs and embedded reports. If storage is unavailable, the
control still works for the current page. Light mode preserves the original
report palette; dark mode adapts authored CSS colors, including live inline
status cards and custom-property shorthands. The toolbar is omitted in embedded
frames, which retain the shared preference.

Presentation resources are injected into HTTP responses. Frozen experiment
sources, generated report files, embedded JSON, original RGB, canvas pixels and
video bytes are not rewritten. Media uses its original colors, without inversion
filters. MP4 range requests still support streaming and seeking. New pages from
older, already-running publishers receive the same theme without changing their
source version. Direct file downloads opened outside this report server do not
receive the injected controls.

`tests/test_report_server.py` checks HTML/HEAD responses, evidence preservation,
asset routing, report-root confinement and unchanged media byte ranges. Real-page
Chromium checks additionally cover desktop/mobile appearance, preference behavior,
live updates and replay interactions; operational evidence stays outside Git.
