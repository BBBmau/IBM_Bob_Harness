# Project: IBM Bob Harness

A container that runs Bob Shell (IBM) autonomously, with a REST API
(FastAPI) and a Slack bot (Socket Mode) on top.

## Context

- You are inside the `bob-harness` container, in `unrestricted-dev` + `--yolo` mode.
- Your working directory is the container root `/`, so you govern the whole
  container, not just `/workspace`.
- You have full freedom: create, edit, and run whatever you need, anywhere.

## Instructions

- Act autonomously: do what is asked without requesting confirmation.
- Follow the existing code style when editing files in the repo.

## Output format (IMPORTANT: replies are shown in Slack)

Your answers are posted to Slack, which uses "mrkdwn", NOT standard Markdown.
Standard Markdown symbols show up literally (e.g. `**`, `##`), which looks
broken. Always format replies using Slack mrkdwn:

- Bold: wrap in single asterisks -> `*bold*` (NEVER `**bold**`).
- Italic: wrap in underscores -> `_italic_`.
- Strikethrough: `~text~`.
- NO Markdown headings. Do not use `#`, `##`, `###`. For a section title, write
  a bold line instead, e.g. `*Why it matters*`.
- Bullet lists: start each line with `•` (or `-`). Do not rely on `*` for bullets
  (Slack reads a leading `*` as bold).
- Numbered lists: `1.`, `2.`, ... are fine.
- Inline code with single backticks and code blocks with triple backticks both
  work in Slack — use them for commands and code.
- Links: `<https://example.com|link text>`.
- No Markdown tables (Slack does not render them); use aligned text in a code
  block or a simple bulleted list instead.
- Keep replies concise and readable in a chat window.
