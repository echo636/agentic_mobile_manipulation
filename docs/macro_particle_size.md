# Authored macro particle size

In the pinned OmniGibson 3.9.2 runtime, macro particle initialization enlarges
small templates until their bounding-sphere radius reaches 1 cm. The native
dicing sampler then requires every source AABB extent to be at least 2 cm.
R313 measured the failing half green onion at 1.464 cm thick, with its native
source scale unchanged. The diced template had been enlarged by 1.626 times.

The catalog adapter sets `MIN_PARTICLE_RADIUS` to zero before loading the
scene, preserving each template's authored geometry. The original particle
sampler, transition rules, source objects, task goals and evaluator remain in
use. The process-local setting is restored on close; shared simulator source
and assets are not edited. Scope is the pinned OG 3.9.2 / Isaac Sim 5.1 runtime.

This changes the simulator's minimum-size heuristic and consequently particle
sizes and counts relative to the enlarged native default. Every new run records
`macro_particle_size_policy.json` and includes the policy in its provenance.
Historical native-default attempts retain their original configuration.

R317 is the direct native control with this setting and the original sampler.
Complete acceptance still requires an autonomous task run and the independent
official evaluator; a successful cutting control alone is not task success.
