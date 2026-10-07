# Independent manipulation system

This is a new project, not a fork of the source repositories. Keep simulator imports inside the OmniGibson adapter. The core must run on CPU without simulator dependencies.

The maintained origin is https://github.com/echo636/agentic_mobile_manipulation.git, explicitly authorized by the user. Develop this repository; do not push to reference repositories. Keep generated runs, assets, credentials and environments outside Git. Current observation is four fixed RGB cameras (front/back/left/right); no wrist camera or robot rotation is used to collect surround views.

The default model protocol starts with initialize, which returns the prepared episode's current four-camera snapshot without a reset or extra render. Each act/look response supplies the next snapshot; wait is an action when simulation time must advance. The minimal profile exposes initialize/look/act/finish, and skills adds list_skills/read_skill. Keep observe and start/get/cancel observation jobs confined to legacy workflow compatibility. Default prompts and skills must not require those hidden tools or an extra observation before finish. All client prompts come from vision_policy.system_prompt.

Never label a mock, scripted policy, oracle-state experiment, or symbolic execution run as a visual autonomous policy or an official challenge submission. Record source, assets, task instance, host, interpreter, GPU UUID, PID/unit, configuration and validation level for each run. A tool success or agent finish claim is not task success: use the independent task evaluator.

Keep operational steps, faults and fixes in journals and structured records; do not create an HTML report for every operation. The existing retest32_v7 replay URL shows only the historical 32 tasks; use a separate summary page for current comparisons. Preserve bookmarked replay links, distinguish cohorts, and publish compact display data rather than full evaluator payloads.

Do not log credentials. Do not modify upstream source snapshots or unrelated environments. Use bounded actions, structured errors, serialized simulator access and immutable episode records. Preserve failure evidence.
