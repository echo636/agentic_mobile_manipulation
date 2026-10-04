# Private online occupancy mapper

`mapping_worker.cpp` uses Cartographer 2.0's `ProbabilityGrid` and native
`ProbabilityGridRangeDataInserter2D` directly. It constructs a fresh map from
observed camera rays and privileged simulator poses. It does **not** implement
scan matching, pose-graph optimization, relocalization, or a full SLAM system.
It never reads a NavMesh, scene traversability raster, or prebuilt map.

The source reference in `upstream/` is an exact snapshot of the Cartographer
integration from `dadwadw233/habitat-gs`, branch `jiarui/memory-slam`, commit
`136950b2fb419b90d2607d4cfc6832f3957eeaa5`; see its license and source manifest.
This is distinct from the older `jinkai/harness` NavMesh-based GT navigator.
Our process adapter retains its private online mapping separation while using
the native range inserter directly for ideal localization.

Each NDJSON update carries a monotonically increasing sequence, a simulation
timestamp, world base pose `[x,y,yaw]`, and camera scans. Each scan contains its
actual base-frame `origin` and explicit `returns` / `misses` lists of XYZ
endpoints. Invalid rays are omitted. Per-camera origins preserve occlusion;
replacing them with the base origin would clear space the camera never saw.

The output is an immutable occupancy snapshot per update: row-major bytes
`0=observed free`, `100=occupied`, `255=unknown`, with columns increasing world
X and rows increasing world Y. `origin` is the world center of cell `[0,0]`.
Native probability updates use hit probability 0.55 and miss probability 0.49;
the 0.5 posterior boundary separates occupied and free evidence. A persistent
sensor-hit overlay immediately blocks newly observed obstacles even when prior
free evidence was saturated. Hits win over other cameras' misses in the same
capture; three later captures with valid free rays mark that formerly occupied
cell free, overriding saturated historical odds. Lack of visibility does not
remove a hit. Ray cells use the same native subpixel rasterizer as Cartographer.
This temporal layer also derives exclusively from sensor rays.
Repeated simulation timestamps are accepted with increasing update sequences.
An initial empty capture returns an all-unknown map. No unknown
cells are cleared around the robot. Footprint inflation belongs to the planner.

Builds and runtime libraries live outside Git in a project-owned runtime prefix.
The build may read an existing Cartographer toolchain; it never modifies it.
The installed runtime must have its own shared-library closure and manifest.
Set `MAS_CARTOGRAPHER_RUNTIME` to that prefix. Runtime failure is reported; no
GT-map fallback is permitted.

The Eigen headers **must** match the static library build. The deployment tested
here uses Eigen 3.4.0; an existing Conda environment had changed to Eigen 5.0.1,
which linked successfully but corrupted native point-cloud/grid behavior. Pass
`-DMAS_CARTOGRAPHER_EIGEN_INCLUDE_DIR=/project/build_dependencies/eigen3` when
configuring against the reused static library. `package_runtime.py` copies the
non-system ELF dependency closure and records source, binary, static library,
Eigen headers, compiler flags, and dependency hashes. It never installs into or
alters the source toolchain. Set the runtime environment variable explicitly for
frozen episode source snapshots; source-relative runtime discovery is only a
local development convenience.
