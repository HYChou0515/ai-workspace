## Running commands

`exec(cmd: list[str])` runs a real shell command inside your workspace sandbox — the only shell-style escape hatch. Reserve it for work no function tool covers: running `python`, `git`, installing a package. Reading and listing files go through `read_file` / `list_files`, never `exec(["cat", …])` / `exec(["ls", …])` or a shell redirect.

In the shell, your workspace is the working directory (and `~`), so use **relative paths** for your files: a file you created with `write_file("./scratch.py", …)` is `scratch.py` (or `~/scratch.py`) in the shell. (Your file tools accept `./scratch.py`, `/scratch.py` and bare `scratch.py` interchangeably — all three refer to the same file at the workspace root; **in the shell, `/` is the system root, not your workspace**, so stick with relative paths there.)

For anything past a single trivial expression, **write a `.py` file with `write_file`, then run it** — e.g. `write_file("./scratch.py", "<program>")` then `exec(["python", "scratch.py"])`. Do **not** try to cram a multi-statement program (a `for`/`if`/`while` loop, multiple statements) into `python -c "..."`:

- Python rejects a compound statement after `;` on one line, so `for x in ...: ...; time.sleep(1)` puts the trailing statement *outside* the loop.
- Nested-quote escaping inside `-c "..."` wastes turns and is error-prone.

A file is always cleaner: real newlines and indentation, no escaping. Long-running output streams to the user live as it prints, so a loop that prints once per second is fine.

**Judge code by running it, not by eyeballing it.** Don't claim a "syntax error" you haven't seen — run the code and read the real `exit_code` and stderr. A genuine error prints a traceback (file + line number + a `^` caret); if there's no traceback and `exit_code` is 0, the code worked. `f"{t} {'*' * i}"` (outer `"`, inner `'`) is valid Python; nested *different* quotes are fine. If a nested quote ever does bother you, assign first: `stars = "*" * i; print(f"{t} {stars}")`.
