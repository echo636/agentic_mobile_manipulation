# Cartographer Worker

Native worker code is split by responsibility:

- `worker.cpp` owns Cartographer lifecycle, trajectory handoff, and artifact publication.
- `scan.{h,cpp}` owns the NDJSON scan protocol and conversion to Cartographer range data.
- `delayed_relocalization.{h,cpp}` owns odometry-frame point accumulation and the external Open3D registration protocol.

The `habitat-cartographer` Conda environment contains Cartographer 2.0.0 and
its CUDA runtime dependencies. Activate it before building or running an
external worker:

```bash
conda activate habitat-cartographer
```

The Habitat bridge writes one depth-derived input stream per session when
`HAB_CARTOGRAPHER_ENABLED=1`. The worker consumes:

```text
data/nav_artifacts/cartographer/<session-id>/metadata.json
data/nav_artifacts/cartographer/<session-id>/scans.jsonl
```

`scans.jsonl` supplies sequence, timestamped ranges, ring HFOV, and source
camera HFOV. The same stream may also carry control records such as
`{"type":"check_relocalization_prior"}`; these ask the worker to re-check an
already recorded graph-memory node prior without waiting for another scan. The
bridge captures four temporary cardinal depth views at a single simulator
instant to make a 360-degree ring. The Python exporter spaces beams uniformly
in Cartographer's angular frame, then projects each beam through the pinhole
camera model to choose the depth column. The worker must interpret those ranges
at the same angular positions.

`HAB_CARTOGRAPHER_USE_ODOMETRY=1` additionally sends rebased evaluator
odometry to a Cartographer `odometry` sensor. With `0`, the input remains
scan-matching-only. Neither mode sends a simulator pose as a range observation
or map-frame transform. The bridge closes a session only after draining its
input queue, then writes `summary.json`.

Set `HAB_CARTOGRAPHER_ODOMETRY_MODE=authoritative` with
`HAB_CARTOGRAPHER_USE_ODOMETRY=1` to use timestamp-matched odometry as the
Cartographer 2D local trajectory pose. This mode requires the patched
Cartographer submodule under `third_party/cartographer`: its
`LocalTrajectoryBuilder2D` stores incoming odometry, skips RTCS/Ceres scan
matching for accumulated range data, inserts submaps at the odometry pose, and
emits local SLAM callbacks whose pose is authoritative odometry. Normal
`cartographer_input` mode remains compatible with the unpatched Conda package.

For LiDAR-only SuperOdom, use the explicit non-oracle source instead:

```bash
HAB_CARTOGRAPHER_USE_ODOMETRY=1
HAB_CARTOGRAPHER_ODOMETRY_SOURCE=superodom
HAB_SUPERODOM_ENABLED=1
HAB_SUPERODOM_WORKER_COMMAND='your_ros2_adapter {input_path} {pose_path} {session_id}'
```

The adapter renders four pinhole depth views at one post-action state and
samples a VLP-16 cloud with 16 manufacturer elevation channels, increasing
REP-103 azimuth, deterministic range-derived intensity, one return per valid
beam, and zero point-relative time. This is an instantaneous stop-and-scan
virtual LiDAR, not a moving spinning-LiDAR simulation. The external adapter
must append a pose record containing the same `sequence` and `timestamp_s` to
`superodom_poses.jsonl`; the bridge waits up to `HAB_SUPERODOM_POSE_TIMEOUT_S`
(default 5 seconds) before it submits the matching Cartographer scan. It never
falls back to simulator ground-truth odometry in this mode.

SuperOdom poses use ROS REP-103 (`+X` forward, `+Y` left, `+Z` up), matching
this worker's tracking axes. The bridge projects the external local SE(3) pose
to `x`, `y`, yaw, and Cartographer performs its existing first-sample rebase.
The external pose frame must remain continuous; do not write globally corrected
or relocalized poses into this stream.

Use the Conda prefix for native builds instead of linking its libraries into
the Habitat Python process:

```bash
cmake -DCMAKE_PREFIX_PATH="$CONDA_PREFIX" ...
```

Build the bundled worker against the Conda environment:

```bash
CC="$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-cc" \
CXX="$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-c++" \
cmake -S tools/cartographer -B /nas1/home/jrguo/habitat-cartographer-build
cmake --build /nas1/home/jrguo/habitat-cartographer-build
```

For authoritative odometry, build and install the patched Cartographer
submodule into a separate prefix, then point the worker at that prefix. The
applied diff is also stored at
`tools/cartographer/patches/authoritative_odometry.patch` so a fresh submodule
checkout can be re-patched reproducibly:

```bash
git -C third_party/cartographer apply ../../tools/cartographer/patches/authoritative_odometry.patch

CC="$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-cc" \
CXX="$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-c++" \
cmake -S third_party/cartographer -B /nas1/home/jrguo/cartographer-authoritative-build \
  -DCMAKE_PREFIX_PATH="$CONDA_PREFIX" \
  -DCMAKE_INSTALL_PREFIX=/nas1/home/jrguo/cartographer-authoritative-prefix \
  -DCMAKE_POLICY_DEFAULT_CMP0074=NEW \
  -DCUDAToolkit_ROOT=/nas1/home/jrguo/cuda-12.9-compat \
  -DLUA_INCLUDE_DIR="$CONDA_PREFIX/include" \
  -DLUA_LIBRARY="$CONDA_PREFIX/lib/liblua.so" \
  -DCMAKE_CXX_FLAGS="-I$CONDA_PREFIX/include -include /nas1/home/jrguo/cartographer_absl_compat.h"
cmake --build /nas1/home/jrguo/cartographer-authoritative-build
cmake --install /nas1/home/jrguo/cartographer-authoritative-build

HAB_CARTOGRAPHER_PREFIX=/nas1/home/jrguo/cartographer-authoritative-prefix \
CC="$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-cc" \
CXX="$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-c++" \
cmake -S tools/cartographer -B /nas1/home/jrguo/habitat-cartographer-build \
  -DCMAKE_PREFIX_PATH="$CONDA_PREFIX"
cmake --build /nas1/home/jrguo/habitat-cartographer-build
```

The CMake target restores the required `GLOG_USE_GLOG_EXPORT` compile
definition missing from the package's exported CMake metadata. The worker
configures a 2D `depth_scan` range sensor, disables IMU, and conditionally adds
the odometry sensor described above. It uses a bounded online correlative
matcher to recover from initial turn-time extrapolation lag. After each
local-SLAM callback, it rewrites `occupancy.png`, persists an immutable
`occupancy_step<sequence>.png` snapshot, appends one record to
`occupancy.png.updates.jsonl`, and records the scan-matched global map pose plus
submitted odometry yaw in `occupancy.png.poses.jsonl`. The matching
`occupancy.png.render.json` records the map-frame-to-PNG transform for graph
annotation. It writes final Cartographer state
to `occupancy.png.pbstream` when stdin closes. Benchmark replay selects the
latest snapshot at or before its displayed simulator step.

For `slam_point_occ`, the bridge starts one dual-stream worker instead of two
worker processes. Both visual and planning scans are written to the same FIFO
as stream-labeled records:

```json
{"stream":"visual","sequence":42,"timestamp_s":0.7,"ranges_m":[2.0,2.1]}
{"stream":"planning","sequence":42,"timestamp_s":0.7,"ranges_m":[1.8,1.9]}
```

The worker publishes visual artifacts under `visual/` and projected-depth
planning artifacts under `planning/`. During retrieve, visual delayed FPFH
relocalization remains authoritative; when it accepts an old-from-local
transform, the planning trajectory is initialized against its own frozen
planning pbstream with that same transform and reports
`localized_from_visual`.

The renderer maps Cartographer X-forward/Y-left into Habitat's top-down
X-right/Z-down display convention. It flips the occupancy pixels and red marker
together horizontally; marker yaw stays derived from Cartographer's global
 local-SLAM pose so heading and horizontal motion remain consistent.

Delayed FPFH relocalization does not rebase an already initialized Cartographer
trajectory. After an accepted odometry-frame registration, the worker retires
that trajectory from publication, creates a replacement trajectory with its
initial pose relative to the frozen map, and starts it at the current scan. It
continues to serve the last committed frozen-map render while the replacement's
local-SLAM callback verifies the requested relative transform within the
anchoring tolerances. Retired submaps are excluded from the current occupancy
render but keep receiving scans until shutdown so the collator cannot stall.
For graph-memory node-prior relocation, the worker binds the latest
provisional local agent pose when it starts the attempt. Sparse-prior matching
therefore constrains the transformed agent pose to the reported node region,
not the current episode origin.

Each node-prior control snapshots its `tool_seq`, prior JSON, report-time local
agent pose, and recent source window. A newer report received during matching
supersedes the older result before trajectory handoff, so a shared prior file
cannot change an in-flight attempt. Reports received during anchoring are
ignored as redundant. Once localization is committed, a later explicit report
may start a corrective attempt; rejection preserves the committed alignment,
while acceptance creates another anchored trajectory. All retired visual and
planning trajectories continue receiving their stream until shutdown to keep
Cartographer's collator advancing.

Relocalization matches the frozen exploration occupancy cells against a
temporary 5 cm Cartographer probability grid rebuilt from scans along the most
recent 8 m of retrieval trajectory. Window length is translational
scan-matched pose arc length, so turn-in-place scans remain available without
consuming distance. The window is selected before fusion; old cells from
earlier or looped portions of the active trajectory therefore cannot leak into
the source cloud. Configure the length with
`HAB_CARTOGRAPHER_RELOCALIZATION_SOURCE_WINDOW_M`.

The temporary grid reuses the worker's range limits and Cartographer's native
probability-grid inserter: returns at 0.15-8 m update endpoint hit odds, farther
finite returns become 5 m free-space misses, and ray traversal updates miss
odds. Only return cells reaching probability 0.65 are exported. This suppresses
one-off depth endpoints contradicted by later free-space observations while
retaining repeatedly observed walls. It uses the stored scan-matched poses and
does not rerun trajectory optimization.

If the recent-window occupancy has fewer than 120 cells for a sparse
node-prior attempt, or fewer than 600 cells for an accumulated attempt, the
worker falls back to deduplicated endpoints from the same recent window.
Attempt JSON records `source_representation` and `source_window`, including
requested/actual length, sequence range, scan/return/miss counts,
observed/occupied-cell counts, occupancy threshold, and grid build duration.
Occupancy-backed attempts also retain the valid window return endpoints as
`relocalization_raw_attempt<N>.xy`. The fallback thresholds can be overridden
with `HAB_CARTOGRAPHER_RELOCALIZATION_SPARSE_OCCUPANCY_MIN_POINTS` and
`HAB_CARTOGRAPHER_RELOCALIZATION_OCCUPANCY_MIN_POINTS`.

To inspect a relocation session afterward, run:

```bash
python tools/cartographer/render_relocalization_visualization.py \
  data/cartographer/<session-id>/visual \
  -o /tmp/relocalization_visualization.html
```

The relocalizer command defaults to:

```bash
conda run --no-capture-output -n habitat-gs python tools/cartographer/relocalize.py
```

At startup, delayed FPFH sessions run the configured command with
`--check-open3d`. If Open3D is unavailable, the worker writes
`status:"error"` with `reason:"open3d_missing"` to
`occupancy.png.localization.json` and exits before ambiguous localization can
be reported.

When graph memory is enabled, every newly created node receives a stable,
monotonically increasing creation-order ID. Creating a node, or updating its
`pano_images`, stores the latest scan-matched Cartographer pose with that node.
Movement responses attach a derived occupancy image with every anchored node
drawn as its creation-order badge, plus an ordered ID-to-full-name text mapping.
Overlapping badges are displaced with leader lines rather than omitted. Nodes
without a rendered Cartographer pose remain unanchored and are not drawn.

`run_worker_tmux.sh` launches each session's native Cartographer worker in a
separate `cartographer_<session-id>` tmux session. The bridge writes ordered
scan records to a per-session FIFO; closing the simulator session closes that
FIFO and lets Cartographer serialize its final map state.
