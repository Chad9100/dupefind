# Roadmap

Estimates are rough working hours for one person.

## v0.1 (done)

- [x] `dupefind scan` with size, first-64-KiB and full SHA-256 passes
- [x] `--min-size`, repeatable `--include` / `--exclude` globs (exclude prunes folders)
- [x] Symlinks and Windows junctions skipped; hard links counted once
- [x] Unreadable files and folders reported without aborting the scan
- [x] Human report with wasted space, and `--json`
- [x] `dupefind plan` with `--keep oldest|newest|shortest-path|first-in`
- [x] `dupefind apply` with quarantine (default), `--delete` and `--dry-run`;
      every file re-hashed first, manifest of moves, quarantine skipped by scans
- [x] pytest suite; CI on Ubuntu and Windows, Python 3.10 / 3.12 / 3.13

## v0.2: restore from quarantine (about 3 h)

- [x] `dupefind restore QUARANTINE_DIR` moves files back using `manifest.jsonl`
- [x] `--dry-run`, refuses to overwrite a file that now exists at the original path
- [x] `dupefind purge QUARANTINE_DIR --older-than 30d` to empty old quarantines

## v0.3: progress and speed (about 4 h)

- [x] Progress line on stderr for long scans (files seen, bytes hashed),
      off when not a TTY or with `--quiet`
- [x] Hash files in a thread pool (`-j` / `--jobs N`, default 1); same
      result for any N
- [ ] Benchmark `--jobs` on a large tree (SSD, HDD, network share) and pick
      a better default than 1 (split out of the item above)
- [ ] Optional hash cache keyed by path, size and mtime (`--cache FILE`) so
      repeat scans skip unchanged files

## v0.4: smarter plans (about 3 h)

- [ ] `--keep-pattern GLOB` / `--remove-pattern GLOB` to prefer or target
      copies by path (for example "always remove from Downloads")
- [ ] Keep strategies combinable as tie-breakers (`--keep first-in,oldest`)
- [ ] `dupefind plan --from-scan scan.json` to plan from saved `--json` output

## v0.5: link instead of delete (about 3 h)

- [ ] `apply --hardlink` replaces extras with hard links to the kept copy
      (same volume only, checked first)
- [ ] `apply --symlink` as an opt-in alternative where hard links are impossible
- [ ] Clear errors on file systems that do not support links

## v0.6: reporting (about 3 h)

- [ ] `--sort size|count|path` and `--top N` for the human report
- [ ] CSV output (`--csv`) for spreadsheets
- [ ] Per-folder summary: which folders hold the most duplicate data

## v0.7: robustness (about 2 h)

- [ ] Long path support on Windows (`\\?\` prefix) for very deep trees
- [ ] Detect when the quarantine folder is on another drive and warn that
      moves become copies
- [ ] `--one-file-system` to stay on the starting device

## v1.0: release (about 2 h)

- [ ] Publish to PyPI with a trusted-publishing release workflow
- [ ] Man-page style `--help` examples and a short asciinema-style demo in the README
