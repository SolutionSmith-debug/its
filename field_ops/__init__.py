"""Field-Ops workstream — Mac-side half of the portal field-operations system.

The portal SPA + Worker (under safety_portal/) write operational data (jobs, crew,
tasks, time, equipment, materials, checklist instances) to D1 SEND-FREE; this package
holds the Mac-side daemon (fieldops_sync) that mirrors that data UP to Smartsheet as the
operator-visible system of record and reconciles the portal-job-create → ITS_Active_Jobs
inversion. See the program plan (Field-Ops Expansion of the Evergreen Safety Portal).
"""
