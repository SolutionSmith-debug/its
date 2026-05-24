# Prompt samples

Executed-artifact samples that prompts reference as few-shot anchors.
Samples are version-controlled.

## Convention

- Each sample's filename includes the source project + date so provenance
  is obvious. Format: `<source>_<project-slug>_<YYYY-MM-DD>.md`.
- Samples are loaded as plain string content by the consuming prompt
  (the prompt embeds or references the sample's text body).
- Samples are immutable once committed. If a sample changes, add a new
  dated file rather than editing in place — prompts that reference the
  old sample must continue to load reproducibly.

## Cross-reference index

| Sample                                          | Used by                          | Notes                                                            |
|-------------------------------------------------|----------------------------------|------------------------------------------------------------------|
| `legacy_wpr_gates_solar_2016-03-12.md`          | `safety_weekly_generate.md`      | Structural anchor for WPR layout (header, incident table, weather, labor, per-trade %). |

## Auto-generated index

<!-- BEGIN AUTO-INDEX -->
| Date | Type | Status | Workstream | Title | PRs |
|------|------|--------|------------|-------|-----|
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [Legacy WPR — Gates Solar, Eure NC — Week of 2016-03-06 to 2016-03-12](legacy_wpr_gates_solar_2016-03-12.md) | _–_ |
<!-- END AUTO-INDEX -->

