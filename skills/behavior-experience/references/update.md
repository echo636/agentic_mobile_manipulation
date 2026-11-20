# Updating the persistent library

Run these commands from the repository with the existing environment sourced.
The CLI never starts a simulator or makes a model request.

A proposal is JSON with these fields:

    {
      "kind": "task",
      "key": "collecting_aluminum_cans",
      "protocol": "oracle_waypoints",
      "description": "Reusable can placement experience and its conditions.",
      "body": "A complete, curated Markdown document, with real newlines.",
      "runs": ["/absolute/path/to/a/finished/run"],
      "claim": "engineering_success",
      "expected_revision": 1,
      "action_keys": ["grasp", "place"]
    }

kind is task or action. Task keys are the exact task IDs; action keys describe the
maneuver, such as grasp or open-door. Protocol is oracle_waypoints or rgb_only.
Use partial_or_failure for partial progress, failed attempts or still-unverified
criteria. Whole-task and engineering success claims are checked against saved
independent results. Descriptions and body are authored by the curator, not generated
from a success flag. Source hashes and scores are added by the tool.

Publish the proposal, preserving actual newlines in the JSON body:

    python scripts/manage_experience_skills.py publish --proposal /absolute/path/proposal.json

Read the previous active document and merge useful lessons into the replacement.
The CLI retains prior versions and accumulates source provenance; it does not infer
how to resolve contradictory prose. expected_revision is 0 for a new skill. Concurrent
updates using an old revision fail instead of overwriting newer work.

Manual archival of a historical episode is idempotent:

    python scripts/manage_experience_skills.py archive --run /absolute/path/run

Rollback changes the active pointer while retaining every version:

    python scripts/manage_experience_skills.py rollback --kind action --key grasp --protocol oracle_waypoints --revision 1 --expected-revision 2

An already running episode keeps its frozen documents and hashes after any update
or rollback. Only a later episode gets the new selection. Source manifests live in
revisions alongside SKILL.md; the robot model can read only its allowlisted Markdown,
not private provenance files, other task skills or candidates.

RAX design references, read at commit db744f5950582c7c301f249192c0ed3c76517dd8:

- https://github.com/robot-agent-exploration/rax/blob/db744f5950582c7c301f249192c0ed3c76517dd8/skills/experience-memory/SKILL.md
- https://github.com/robot-agent-exploration/rax/blob/db744f5950582c7c301f249192c0ed3c76517dd8/skills/continual-learning/SKILL.md

The document library continues to provide planning experience. The separate opt-in
oracle_compiled profile also supports executable subroutines; see
[executable.md](executable.md). This project does not adopt RAX's real-robot merge
workflow or treat upstream instructions as authorization to commit or push.
