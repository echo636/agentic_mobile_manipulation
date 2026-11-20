# bottle_grasps

Existing upright elongated-object mesh-section grasp program. Select directly with parameters.object_id and arm after get_manip_geometry. Optional parameters.control_steps={pregrasp: estimated steps, contact_close: estimated steps}. Uses the original bottle_grasps sampler and native measured contact offset/aperture; returns ranked pregrasp/contact candidates. Hand must already be open. Reuse as-is, adapt it or write another program when task/shape differs.

Read this source before selecting it. Recompute geometry from the current observation. Draft code is available for reuse and iteration; collection does not certify physical success. Keep failures and source hashes when adapting this program.
