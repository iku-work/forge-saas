# Running a local model on these tickets

What went wrong with Gemma 4 E4B (64k context) on ticket 01, from its session logs,
and how to launch and prompt it so it doesn't happen again.

## What the logs show

| Problem | Evidence | Effect |
|---|---|---|
| **Most of the context is gone before the first word.** | First request already ~23–25k tokens: Claude Code's system prompt, ~30 MCP servers' tools (the small-business plugin), `AGENTS.md`, the memory index, plus the ponytail/frontend-design skills you invoked. Auto-compaction then fires at ~43k. | About **20k tokens** of real working space. The last session compacted **3 times**. |
| **Compaction loses the task.** | After compacting, one session's summary said the goal was "implement **T-001: Monorepo skeleton**" (from `AGENTS.md`'s last line, "Start with T-001"). | It drifted to a different, already-finished task. |
| **Edits made from memory, not from the file.** | `Edit` failed with old strings that never existed (e.g. `/* Font size base layer - to be populated */`), then a `Write` was attempted over an existing file. | Half-applied edits: a declaration written over a comment, a duplicated rule, a clobbered `body {`. |
| **Tool-schema mistakes.** | `Agent` called without `description`; `Write` called with `new_string`. | Wasted turns, more compaction. |
| **Wrong working directory.** | One session was started inside `.scratch/visual-overhaul`. | "File does not exist" on every repo path. |
| **Auto mode asks the same small model for safety checks.** | "gemma4-64k is temporarily unavailable (timed out), so auto mode cannot determine the safety…" | Stalls and retries. |
| **Extra skills add instructions that compete with the ticket.** | ponytail ("shortest diff") and frontend-design ("distinctive design") were loaded. | More tokens; goals that pull against "follow the ticket exactly". |

The model did fine when the step was "put these exact values in these exact places"
(ticket 01 steps 1–2). It broke when it had to edit around existing code.

## How to launch it

From the **worktree root**, in bash, with whatever env you already use to point Claude Code at
the local server (`ANTHROPIC_BASE_URL`, and an `ANTHROPIC_API_KEY`; `--bare` only reads the key from that variable):

```bash
cd /c/Users/IK/Documents/saas-template/.claude/worktrees/visual-overhaul
claude --bare --strict-mcp-config --permission-mode acceptEdits \
  --model gemma4-64k \
  --append-system-prompt-file .scratch/visual-overhaul/README.md
```

- `--bare` skips hooks (ponytail's start-up hook), plugins, auto-memory and `AGENTS.md` discovery.
- `--strict-mcp-config` with no `--mcp-config` loads no MCP servers.
- `--append-system-prompt-file` puts the ticket rules in the system prompt, so compaction can't drop them.
- `--permission-mode acceptEdits` lets it edit files without asking but still asks before shell commands,
  and doesn't need the model to judge its own safety (no auto mode).

Don't invoke `/ponytail`, `/frontend-design` or any other skill in these sessions.

If your runtime lets you raise the context (e.g. 128k) or run a larger model, do that: both help
more than any prompt wording.

## How to prompt it

**One small item per session.** When it's done, review, commit, then `/clear` (or start a new session).
Never let a session reach compaction on these tickets.

Template (fill in the angle brackets):

```text
Task: ticket <NN>, step <S>, only <the one item, e.g. "the popover component">.

1. Read .scratch/visual-overhaul/PROGRESS.md and the "Step <S>" section of
   .scratch/visual-overhaul/issues/<ticket file>. Tell me in one sentence what you will change.
2. Read <file path> with the Read tool.
3. Make the change with the Edit tool. Copy old_string exactly from the Read output in this
   session; never type it from memory. If Edit says "String to replace not found", Read the
   file again and retry. Never use Write on a file that already exists.
4. Run: <check command from the ticket, e.g. pnpm --filter @forge/ui type-check>
5. Show me `git diff` and stop. Do not edit any other file. Do not commit. Do not use the Agent tool.
```

For anything in `packages/ui/styles/tokens.css`, the check in step 4 must be
`pnpm --filter @forge/ui build && pnpm --filter marketing build`: it is the only check that parses the CSS.

After each item: read the diff yourself (or ask Claude to), then
`pre-commit run --files <changed files>` (see `PROGRESS.md` for the path), commit, and tick the item in `PROGRESS.md`.

## What to give it, and what not

Good fits: changing classes in one component to match a ticket's table; adding translation keys;
renaming copy (ticket 03); replacing one hardcoded colour class with a token in one file.

Poor fits: anything that restructures a file or spans several files (tickets 04–13 beyond their first
steps, the shell, the hero), and the screenshot regeneration in ticket 14.
