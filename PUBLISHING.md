# Publishing Checklist

This repository is intended to publish clean-room protocol facts and original
Python code only.

Before pushing a public repository:

1. Confirm ignored private/vendor material is not staged:

   ```sh
   git status --ignored --short
   ```

   Expected ignored local-only paths include `downloads/`, `extracted/`,
   `harness/captures/`, `tmp/`, `references-cache/`,
   `notes/drc225-disassembly.txt`, `harness/.venv/`, and `harness/tessdata/`.

2. Scan publishable files for local identifiers. Keep the concrete private
   patterns in your shell history or a local scratch file, not in the public
   repository:

   ```sh
   rg -n "<private-ip>|<scanner-bonjour-id>|<scanner-serial>|<private-name>|<private-address>|<private-account-id>" \
     -g '!downloads/**' -g '!extracted/**' -g '!harness/captures/**' \
     -g '!tmp/**' -g '!references-cache/**' \
     -g '!notes/drc225-disassembly.txt' -g '!harness/.venv/**' \
     -g '!harness/tessdata/**'
   ```

   The command should return no matches.

3. Check for accidental large publishable files:

   ```sh
   find . -maxdepth 3 -type f -size +1M \
     -not -path './downloads/*' \
     -not -path './extracted/*' \
     -not -path './harness/captures/*' \
     -not -path './tmp/*' \
     -not -path './references-cache/*' \
     -not -path './harness/.venv/*' \
     -not -path './harness/tessdata/*' \
     -not -path './notes/drc225-disassembly.txt' \
     -print
   ```

4. Run the harness checks:

   ```sh
   cd harness
   uv lock --check
   uv run python -m unittest discover -s tests -v
   uv run python -m py_compile cgiscsi.py commands.py discover.py mock_cgiscsi.py scan_to_pdf.py
   ```

5. Review `references.md` instead of committing upstream binaries or source
   snapshots. Canon driver archives and extracted binaries must stay local.

The project has no existing Git history in this working directory. For a public
release, initialize a fresh repository after the checklist passes so no private
scan output or proprietary extracted material can exist in history.
