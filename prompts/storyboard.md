# Orpheus storyboard handoff

Read analysis.json, context.json and storyboard.schema.json in this folder.
Return only a schema-valid storyboard.json. Do not run or include shell/code.

- Preserve analysis_id, analysis_sha256 and audio_sha256 exactly.
- Ask the user for a visual world if none is provided. Do not infer confirmed facts from unreviewed sections.
- Cover [0, duration_frames) with contiguous, non-overlapping scenes.
- Use still, slow_push, pan_left or pan_right. First transition is cut.
- Crossfades overlay the previous final frame at the beginning of the new scene; they do not shorten the timeline.
- Keep lyric_ids as references. Do not move lyric timings into scenes or alter the original lyrics.
- Prefer beat-aligned scene boundaries when beats are available, within the exact total frame count.
- Name required image IDs and describe their prompts and visual intent. Use one consistent reference image.
- Include no URLs, credentials, code or extra schema properties.

The user imports the result with `mv import-storyboard PROJECT storyboard.json`.
